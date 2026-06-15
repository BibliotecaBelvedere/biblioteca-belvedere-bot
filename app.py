import os
import re
import time
import sqlite3
import requests
import unicodedata
from flask import Flask, request, jsonify
from threading import Thread

app = Flask(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

DB_FILE = "catalogo.db"

STOPWORDS = {
    'che','del','della','delle','degli','dei','dal','dalla','dalle','dagli','dai',
    'nel','nella','nelle','negli','nei','sul','sulla','sulle','sugli','sui','per',
    'con','una','uno','gli','alla','allo','alle','agli','col','coi','tra','fra',
    'non','qui','qua','sua','suo','suoi','sue','mio','mia','miei','mie','tuo',
    'tua','tuoi','tue','questo','questa','questi','queste','quello','quella',
    'quelli','quelle','anche','come','dove','quando','mentre','essere','avere',
    'fare','dire','cerca','cerco','vorrei','voglio','cercare','trovare','libro',
    'libri','testo','testi','parli','parla','parlano','riguarda','riguardano',
    'tratta','trattano','scritto','scritta','uscito','uscita','hai','avete','trova','un',
    'mi','dai','dacci','dimmi','trovami','cercami','sono','ci','adatti','alle',
    'crechi','creca','su','di','da','a','in','qualcosa',
    'mostrami','elenco','lista','autori','autore','volumi','volume','titoli','titolo'
}

# DIZIONARIO DELLE MACRO-AREE TEMATICHE (Espansione della ricerca)
# DIZIONARIO DELLE MACRO-AREE TEMATICHE (Espansione della ricerca aggiornato)
TEMI_ESPANSI = {
    "cucin": ["cucin", "ricett", "gastronom", "diet", "piatt", "aliment", "mangiare"],
    "teatr": ["teatr", "commedia", "tragedia", "dramma", "atto", "scena", "copione"],
    "amor": ["amor", "passione", "sentimento", "innamor", "affetto"],
    "bullis": ["bullis", "bullo", "cyberbulli", "violenza", "scuola", "ragazzi", "aggressione"],
    "giallo": ["giallo", "gialli", "thriller", "poliziesc", "assassin", "delitto", "mistero", "indagine"],
    "noir": ["noir", "poliziesco", "hardboiled", "crimine", "indagine", "mistero"],
    "avventur": ["avventur", "azione", "esplorazione", "viaggio", "pericolo", "sopravvivenza"],
    "fantasy": ["fantasy", "fantastico", "magia", "drago", "spada", "creature", "leggenda"],
    "manga": ["manga", "fumetto", "fumetti", "anime", "giappone", "giapponese", "shonen", "shojo"],
    "anime": ["anime", "manga", "animazione", "cartone", "giappone"],
    "fumett": ["fumett", "manga", "albo", "strisce", "vignette", "graphic novel"],
    "albo": ["albo", "albi", "fumetto", "fumetti", "illustrato", "storia"],
    "supereroi": ["supereroi", "supereroe", "marvel", "dc", "fumetto", "fumetti", "eroe", "poteri"]
}

def normalize(s):
    s = str(s).lower().strip()
    for c in ['?', '!', ',', '.', ';', ':', '-', '_', '*', '"', "'"]:
        s = s.replace(c, ' ')
    s = s.replace('č', 'c').replace('š', 's').replace('ś', 's').replace('ā', 'a')
    s = unicodedata.normalize("NFD", s)
    return " ".join("".join(c for c in s if unicodedata.category(c) != "Mn").split())

def inizializza_database():
    file_presenti = os.listdir(".")
    file_reale = None
    for f_name in file_presenti:
        if f_name.lower() == "catalogo.txt":
            file_reale = f_name
            break
            
    if not file_reale:
        return f"ERRORE: Il file catalogo.txt NON esiste su Render! File trovati: {file_presenti}"

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS libri (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                testo_completo TEXT,
                testo_normalizzato TEXT
            )
        ''')
        cursor.execute("DELETE FROM libri")
        
        with open(file_reale, "r", encoding="utf-8-sig", errors="ignore") as f:
            contenuto = f.read().replace("\r\n", "\n").replace("\u00a0", "\n")
            
        pezzi_raw = contenuto.split("[nd]")
        blocchi_effettivi = []
        
        for pezzo in pezzi_raw:
            linee = [l.strip() for l in pezzo.split("\n") if l.strip()]
            linee_pulite = [l for l in linee if "Ordinamento Soggetto" not in l and "Biblioteca:" not in l and "Data e ora:" not in l]
            
            if linee_pulite:
                testo_blocco = "\n".join(linee_pulite)
                blocchi_effettivi.append(testo_blocco)

        for blocco in blocchi_effettivi:
            cursor.execute(
                "INSERT INTO libri (testo_completo, testo_normalizzato) VALUES (?, ?)",
                (blocco, normalize(blocco))
            )
        conn.commit()
        conn.close()
        return f"SUCCESS: Database ricostruito correttamente! Caricati {len(blocchi_effettivi)} libri dal file '{file_reale}'."
    except Exception as e:
        return f"ERRORE TECNICO durante la lettura/scrittura del file: {str(e)}"

# MOTORE DI RICERCA INTELLIGENTE CON ORDINAMENTO PER RILEVANZA
def cerca_nel_db(query):
    q = normalize(query)
    parole_chiave = [w for w in q.split() if len(w) >= 2 and w not in STOPWORDS]
    
    if not parole_chiave:
        return []

    parole_espanse = set()
    ricerca_tematica_attiva = False
    
    for parola in parole_chiave:
        trovato_tema = False
        for radice, sinonimi in TEMI_ESPANSI.items():
            if radice in parola or parola in radice:
                parole_espanse.update(sinonimi)
                ricerca_tematica_attiva = True
                trovato_tema = True
                break
        if not trovato_tema:
            parole_espanse.add(parola)

    parole_espanse = list(parole_espanse)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if ricerca_tematica_attiva:
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole_espanse]
        parametri = [f"%{p}%" for p in parole_espanse]
        sql_query = f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' OR '.join(condizioni)}"
    else:
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole_espanse]
        parametri = [f"%{p}%" for p in parole_espanse]
        sql_query = f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' AND '.join(condizioni)}"
        
    cursor.execute(sql_query, list(parametri))
    righe = cursor.fetchall()
    
    if not righe and not ricerca_tematica_attiva and len(parole_espanse) > 1:
        condizioni_or = ["testo_normalizzato LIKE ?" for _ in parole_espanse]
        parametri_or = [f"%{p}%" for p in parole_espanse]
        sql_query_or = f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' OR '.join(condizioni_or)}"
        cursor.execute(sql_query_or, [f"%{p}%" for p in parole_espanse])
        righe = cursor.fetchall()
        
    conn.close()
    
    if not righe:
        return []

    libri_ordinati = []
    for testo_completo, testo_norm in righe:
        punteggio = 0
        for pk in parole_chiave:
            if pk in testo_norm:
                punteggio += 10
                
        for pe in parole_espanse:
            if pe in testo_norm:
                punteggio += 1
                
        libri_ordinati.append((punteggio, testo_completo))
        
    libri_ordinati.sort(key=lambda x: x[0], reverse=True)
    return [libro[1] for libro in libri_ordinati[:15]]

    # SISTEMA DI CALCOLO DELLA RILEVANZA
    # Diamo un punteggio a ogni libro in base a quante parole chiave ed esatte contiene
    libri_ordinati = []
    for testo_completo, testo_norm in righe:
        punteggio = 0
        # Punti extra se contiene la parola ESATTA digitata dall'utente (es. "bullismo" o "cucina")
        for pk in parole_chiave:
            if pk in testo_norm:
                punteggio += 10  # Peso massimo alla richiesta originale
                
        # Punti standard se contiene i sinonimi espansi
        for pe in parole_espanse:
            if pe in testo_norm:
                punteggio += 1
                
        libri_ordinati.append((punteggio, testo_completo))
        
    # Ordiniamo dal punteggio più alto al più basso e prendiamo i migliori 15
    libri_ordinati.sort(key=lambda x: x[0], reverse=True)
    
    return [libro[1] for libro in libri_ordinati[:15]]

def call_gemini_api(model_name, prompt_text):
    try:
        url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt_text}]}]},
            timeout=8
        )
        return response
    except:
        return None

def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Mi dispiace, nessun volume nel nostro catalogo corrisponde a questa richiesta al momento."
    
    limite_libri = 15
    mostrati_subito = testi_libri[:limite_libri]
    
    context_list = []
    for blocco in mostrati_subito:
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        blocco_corto = " | ".join(linee[:5])
        context_list.append(blocco_corto)
        
    context = "\n---\n".join(context_list)
    
    prompt_completo = (
        "Sei l'assistente ufficiale della Biblioteca Belvedere di Siracusa (SBS0CB).\n"
        "Genera un elenco ordinato e pulito dei libri trovati forniti nel contesto.\n"
        f"Dati estratti dal catalogo:\n{context}\n\n"
        "ISTRUZIONI RIGIDE:\n"
        "1. Filtra i dati mostrando solo i volumi inerenti alla richiesta dell'utente.\n"
        "2. Formato output: **Titolo**, Autore ed eventualmente Collocazione su un'unica riga.\n"
        "3. Concludi sempre con un saluto cordiale e l'invito a venire in biblioteca a Siracusa."
    )

    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    if not response or response.status_code != 200:
        linee_emergenza = [
            "📚 **Biblioteca Belvedere (SBS0CB) - Risultati Ricerca**:\n",
            "Ecco i volumi trovati nel sistema:\n"
        ]
        for blocco in mostrati_subito:
            linee = [l.strip() for l in blocco.split('\n') if l.strip()]
            info_libro = " - ".join(linee[:3])
            linee_emergenza.append(f"• {info_libro}")
        linee_emergenza.append("\n_Nota: Chiedi al bibliotecario per una ricerca completa in sede._")
        return "\n".join(linee_emergenza)
            
    try:
        data = response.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except:
        return "Errore di lettura della risposta dall'intelligenza artificiale."

def send_telegram(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

def async_process_request(chat_id, text):
    try:
        libri_trovati = cerca_nel_db(text)
        reply = ask_gemini(text, libri_trovati)
        send_telegram(chat_id, reply)
    except Exception as e:
        send_telegram(chat_id, "Errore durante l'elaborazione dei dati della biblioteca.")

@app.route("/webhook_biblioteca", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json()
        if not data or "message" not in data:
            return "OK", 200
            
        message = data["message"]
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()
        
        if not chat_id or not text:
            return "OK", 200
            
        if text == "/start":
            send_telegram(chat_id, "Benvenuto nell'assistente della Biblioteca Belvedere! 📚 Scrivimi un autore, un titolo o un argomento (es. libri di cucina, testi sul bullismo, commedie teatrali) per avviare la ricerca.")
            return "OK", 200
            
        thread = Thread(target=async_process_request, args=(chat_id, text))
        thread.start()
        
    except Exception as e:
        pass
            
    return "OK", 200

@app.route("/setup", methods=["GET"])
def setup():
    res = inizializza_database()
    try:
        render_url = request.host_url.rstrip("/")
        resp = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": f"{render_url}/webhook_biblioteca"}, timeout=10)
        tele_res = resp.json()
    except Exception as e:
        tele_res = f"Errore connessione Telegram: {str(e)}"
        
    return jsonify({"status": res, "telegram_response": tele_res})

@app.route("/", methods=["GET"])
def home():
    return "Assistente Biblioteca Pronto.", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
