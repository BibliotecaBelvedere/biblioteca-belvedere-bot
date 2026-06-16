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
    'quelli','quelle','anche','como','dove','quando','mentre','essere','avere',
    'fare','dire','cerca','cerco','vorrei','voglio','cercare','trovare','libro',
    'libri','testo','testi','parli','parla','parlano','riguarda','riguardano',
    'tratta','trattano','scritto','scritta','uscito','uscita','hai','avete','trova','un',
    'mi','dai','dacci','dimmi','trovami','cercami','sono','ci','adatti','alle',
    'crechi','creca','su','di','da','a','in','qualcosa',
    'mostrami','elenco','lista','autori','autore','volumi','volume','titoli','titolo'
}

# POTENZIAMENTO DEI GENERI: Inseriamo gli autori chiave per aiutare il database a pescare i libri giusti per l'AI
TEMI_ESPANSI = {
    "cucin": ["cucin", "ricett", "gastronom", "diet", "piatt", "aliment", "mangiare", "artusi", "cracco", "cannavacciuolo"],
    "teatr": ["teatr", "commedia", "tragedia", "dramma", "atto", "scena", "copione", "pirandello", "de filippo", "goldoni", "shakespeare"],
    "amor": ["amor", "passione", "sentimento", "innamor", "affetto", "steel", "casati modignani", "sparks", "rosa", "romance", "allende", "sveva"],
    "rosa": ["rosa", "amor", "steel", "casati modignani", "sparks", "sentimentale", "passione", "romance"],
    "bullis": ["bullis", "bullo", "cyberbulli", "violenza", "scuola", "ragazzi", "aggressione"],
    "giallo": ["giallo", "gialli", "thriller", "poliziesc", "assassin", "delitto", "mistero", "indagine", "christie", "conan doyle", "camilleri", "simenon"],
    "noir": ["noir", "poliziesco", "hardboiled", "crimine", "indagine", "mistero", "carlotto", "carofiglio", "indridason", "lucarelli"],
    "avventur": ["avventur", "azione", "esplorazione", "viaggio", "pericolo", "sopravvivenza", "salgari", "verne", "cuba"],
    "fantasy": ["fantasy", "fantastico", "magia", "drago", "spada", "creature", "leggenda", "tolkien", "rowling", "martin", "troisi"],
    "manga": ["manga", "fumetto", "fumetti", "anime", "giappone", "giapponese", "shonen", "shojo", "shinzo", "rowell", "panini"],
    "anime": ["anime", "manga", "animazione", "cartone", "giappone"],
    "fumett": ["fumett", "manga", "albo", "strisce", "vignette", "graphic novel", "zerocalcare", "disney", "tex"],
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
        condizioni = []
        parametri = []
        for p in parole_espanse:
            condizioni.append("testo_normalizzato LIKE ?")
            parametri.append(f"%{p}%")
        sql_query = f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' OR '.join(condizioni)} LIMIT 30"
    else:
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole_espanse]
        parametri = [f"%{p}%" for p in parole_espanse]
        sql_query = f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' AND '.join(condizioni)} LIMIT 20"
        
    cursor.execute(sql_query, list(parametri))
    righe = cursor.fetchall()
    
    if not righe and not ricerca_tematica_attiva and len(parole_espanse) > 1:
        condizioni_or = ["testo_normalizzato LIKE ?" for _ in parole_espanse]
        sql_query_or = f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' OR '.join(condizioni_or)} LIMIT 20"
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
    return [libro[1] for libro in libri_ordinati]

def call_gemini_api(model_name, prompt_text):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt_text}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2
            }
        }
        
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=12
        )
        print(f"[GEMINI LOG] Status Code: {response.status_code}")
        return response
    except Exception as e:
        print(f"[GEMINI LOG] Eccezione di rete: {str(e)}")
        return None

def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Mi dispiace, nessun volume nel nostro catalogo corrisponde a questa richiesta al momento."
    
    context_list = []
    for i, blocco in enumerate(testi_libri[:10]):
        context_list.append(f"CANDIDATO #{i+1}:\n{blocco}")
        
    context = "\n\n---\n\n".join(context_list)
    
    prompt_completo = (
        "Sei l'assistente virtuale ufficiale della Biblioteca Belvedere di Siracusa (SBS0CB).\n"
        f"L'utente sta cercando volumi attinenti a: '{user_message}'\n\n"
        f"Analizza attentamente la seguente lista di libri grezzi estratti dal database:\n\n{context}\n\n"
        "REGOLE DI SELEZIONE E FILTRO:\n"
        "1. Agisci da filtro intelligente: seleziona i libri coerenti con la richiesta (es. romanzi d'amore/rosa, gialli, saggi ecc.).\n"
        "2. Se l'utente chiede romanzi d'amore o storie d'amore, includi i romanzi sentimentali di autori come Danielle Steel, Sveva Casati Modignani o simili presenti nella lista.\n"
        "3. Per ogni libro accettato, mostra all'utente il TITOLO COMPLETO, l'Autore e la Collocazione.\n"
        "4. Formato di output richiesto (elenco puntato):\n"
        "   * **Titolo del Libro**, Autore - Collocazione\n"
        "5. Se nessuno dei libri è coerente, rispondi spiegando gentilmente che non ci sono volumi specifici su questo tema.\n"
        "6. Chiudi sempre con un invito cordiale a venire a trovarci in sede a Siracusa."
    )

    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    if not response or response.status_code != 200:
        q_clean = normalize(user_message)
        parole_ricerca = [w for w in q_clean.split() if len(w) >= 3 and w not in STOPWORDS]
        linee_emergenza = ["📚 **Biblioteca Belvedere (SBS0CB) - Risultati Ricerca**:\n"]
        contatore = 0
        
        for blocco in testi_libri:
            testo_norm = normalize(blocco)
            if any(p in testo_norm for p in parole_ricerca) or "steel" in testo_norm or "casati" in testo_norm:
                linee = [l.strip() for l in blocco.split('\n') if l.strip()]
                info_libro = " - ".join(linee[:3])
                linee_emergenza.append(f"• {info_libro}")
                contatore += 1
            if contatore >= 8:
                break
        linee_emergenza.append("\n_Nota: Ti aspettiamo in sede a Siracusa per consultare il catalogo completo._")
        return "\n".join(linee_emergenza)
            
    try:
        data = response.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except:
        return "Errore di lettura dei dati dall'intelligenza artificiale."

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
            send_telegram(chat_id, "Benvenuto nell'assistente della Biblioteca Belvedere! 📚 Scrivimi un autore, un titolo o un argomento per avviare la ricerca.")
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
