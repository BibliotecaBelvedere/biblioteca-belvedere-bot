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

TEMI_ESPANSI = {
    "cucin": ["cucin", "ricett", "gastronom", "diet", "piatt", "aliment", "mangiare", "artusi"],
    "teatr": ["teatr", "commedia", "tragedia", "dramma", "pirandello", "goldoni", "shakespeare"],
    "amor": ["modignani", "steel", "sparks", "allende", "rosa", "sentiment", "passione"],
    "rosa": ["modignani", "steel", "sparks", "allende", "rosa", "sentiment", "passione"],
    "bullis": ["bullis", "bullo", "cyberbulli", "violenza", "scuola", "ragazzi"],
    "giallo": ["giallo", "gialli", "thriller", "poliziesc", "assassin", "delitto", "mistero", "christie", "camilleri"],
    "noir": ["noir", "poliziesco", "crimine", "indagine", "carlotto", "carofiglio", "indridason"],
    "avventur": ["avventur", "azione", "esplorazione", "viaggio", "salgari", "verne"],
    "fantasy": ["fantasy", "fantastico", "magia", "drago", "tolkien", "rowling", "martin"],
    "manga": ["manga", "fumetto", "fumetti", "anime", "giappone", "shinzo", "rowell"],
    "fumett": ["fumett", "manga", "albo", "strisce", "vignette", "zerocalcare", "tex"]
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
        return f"ERRORE: Il file catalogo.txt NON esiste. Trovati: {file_presenti}"

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
        return f"SUCCESS: Caricati {len(blocchi_effettivi)} libri."
    except Exception as e:
        return f"ERRORE: {str(e)}"

def cerca_nel_db(query):
    q = normalize(query)
    parole_chiave = [w for w in q.split() if len(w) >= 2 and w not in STOPWORDS]
    
    if not parole_chiave:
        return []

    parole_espanse = set()
    ricerca_tematica_attiva = False
    
    # FORZATURA AD HOC: Se rileva intenzioni legate al genere Rosa/Amore
    if "amor" in q or "rosa" in q or "sentiment" in q:
        parole_espanse.update(["modignani", "steel", "sparks", "allende", "rosa", "amor", "passione"])
        ricerca_tematica_attiva = True
    else:
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
        # Usiamo OR per raccogliere sia i termini di genere che i cognomi chiave delle autrici
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole_espanse]
        parametri = [f"%{p}%" for p in parole_espanse]
        sql_query = f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' OR '.join(condizioni)} LIMIT 40"
    else:
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole_espanse]
        parametri = [f"%{p}%" for p in parole_espanse]
        sql_query = f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' AND '.join(condizioni)} LIMIT 20"
        
    cursor.execute(sql_query, list(parametri))
    righe = cursor.fetchall()
    conn.close()
    
    if not righe:
        return []

    libri_ordinati = []
    for testo_completo, testo_norm in righe:
        punteggio = 0
        
        # Super-bonus per spingere in alto le autrici rosa se l'utente cerca quel genere
        if "amor" in q or "rosa" in q or "sentiment" in q:
            if "modignani" in testo_norm or "steel" in testo_norm or "sparks" in testo_norm:
                punteggio += 100  # Schizza in cima alla lista!
            if "alberoni" in testo_norm or "saggi" in testo_norm:
                punteggio -= 50   # Penalizziamo i saggi psicologici

        for pk in parole_chiave:
            if pk in testo_norm:
                punteggio += 15
        for pe in parole_espanse:
            if pe in testo_norm:
                punteggio += 2
                
        libri_ordinati.append((punteggio, testo_completo))
        
    libri_ordinati.sort(key=lambda x: x[0], reverse=True)
    return [libro[1] for libro in libri_ordinati]

def call_gemini_api(model_name, prompt_text):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
            "generationConfig": {"temperature": 0.2}
        }
        response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=12)
        return response
    except:
        return None

def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Mi dispiace, nessun volume nel nostro catalogo corrisponde a questa richiesta al momento. Ti invitiamo a consultare il bibliotecario in sede per una ricerca più approfondita."
    
    context_list = []
    for i, blocco in enumerate(testi_libri[:15]): # Alzato a 15 candidati per dare più scelta all'AI
        context_list.append(f"CANDIDATO #{i+1}:\n{blocco}")
        
    context = "\n\n---\n\n".join(context_list)
    
    prompt_completo = (
        "Sei l'assistente virtuale ufficiale della Biblioteca Belvedere di Siracusa (SBS0CB).\n"
        f"L'utente cerca: '{user_message}'\n\n"
        f"Lista libri candidati dal database:\n\n{context}\n\n"
        "COMPITO E REGOLE RIGIDE:\n"
        "1. Filtra con intelligenza i libri: Se l'utente cerca romanzi d'amore o rosa, includi i romanzi sentimentali veri e propri (es. Sveva Casati Modignani, Danielle Steel, Nicholas Sparks, o storie d'amore narrative).\n"
        "2. Escludi tassativamente i saggi scientifici, psicologici o sociologici (es. Francesco Alberoni, saggi sull'innamoramento) se l'utente chiede esplicitamente romanzi.\n"
        "3. Seleziona un massimo di 6-8 volumi tra i più calzanti.\n"
        "4. Per ogni romanzo coerente trovato, mostra un elenco puntato usando questo preciso formato:\n"
        "   * **Titolo del Libro**, Autore - Collocazione\n"
        "5. IMPORTANTE: Aggiungi SEMPRE alla fine dell'elenco una nota fissa che specifichi che la risposta è parziale e che invita l'utente a consultare il bibliotecario in sede a Siracusa per ulteriori dettagli o informazioni complete."
    )

    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    # EMERGENZA BLINDATA CON NOTA OBBLIGATORIA
    if not response or response.status_code != 200:
        q_clean = normalize(user_message)
        parole_ricerca = [w for w in q_clean.split() if len(w) >= 3 and w not in STOPWORDS]
        linee_emergenza = ["📚 **Biblioteca Belvedere (SBS0CB) - Risultati Ricerca (Parziale)**:\n"]
        contatore = 0
        
        for blocco in testi_libri:
            testo_norm = normalize(blocco)
            if "alberoni" in testo_norm and ("amor" in q_clean or "rosa" in q_clean):
                continue
                
            if any(p in testo_norm for p in parole_ricerca) or "modignani" in testo_norm or "steel" in testo_norm:
                linee = [l.strip() for l in blocco.split('\n') if l.strip()]
                info_libro = " - ".join(linee[:3])
                linee_emergenza.append(f"• {info_libro}")
                contatore += 1
            if contatore >= 8:
                break
        linee_emergenza.append("\n_Nota: Questa risposta è parziale. Ti invitiamo a consultare il bibliotecario in sede a Siracusa per ulteriori notizie, informazioni e per consultare il catalogo completo._")
        return "\n".join(linee_emergenza)
            
    try:
        data = response.json()
        output_ai = data['candidates'][0]['content']['parts'][0]['text']
        if "bibliotecario" not in output_ai.lower():
            output_ai += "\n\n_Nota: Questa risposta è parziale. Si invita a consultare il bibliotecario in sede per ulteriori notizie e informazioni complete._"
        return output_ai
    except:
        return "Errore di lettura dei dati. Si invita a consultare il bibliotecario in sede a Siracusa."

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
    except:
        send_telegram(chat_id, "Si è verificato un errore. Consultare il bibliotecario in sede.")

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
    except:
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
        tele_res = f"Errore connessione: {str(e)}"
    return jsonify({"status": res, "telegram_response": tele_res})

@app.route("/", methods=["GET"])
def home():
    return "Assistente Biblioteca Pronto.", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
