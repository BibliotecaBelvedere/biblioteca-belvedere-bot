import os
import re
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
        return "ERRORE: Il file catalogo.txt NON esiste."
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS libri (id INTEGER PRIMARY KEY AUTOINCREMENT, testo_completo TEXT, testo_normalizzato TEXT)')
        cursor.execute("DELETE FROM libri")
        
        with open(file_reale, "r", encoding="utf-8-sig", errors="ignore") as f:
            contenuto = f.read().replace("\r\n", "\n").replace("\u00a0", "\n")
            
        pezzi_raw = contenuto.split("[nd]")
        blocchi_effettivi = []
        for pezzo in pezzi_raw:
            linee = [l.strip() for l in pezzo.split("\n") if l.strip()]
            linee_pulite = [l for l in linee if "Ordinamento" not in l and "Biblioteca:" not in l and "Data e ora:" not in l]
            if linee_pulite:
                testo_blocco = "\n".join(linee_pulite)
                blocchi_effettivi.append(testo_blocco)

        for blocco in blocchi_effettivi:
            cursor.execute("INSERT INTO libri (testo_completo, testo_normalizzato) VALUES (?, ?)", (blocco, normalize(blocco)))
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

    # Intercettiamo i macro generi per fare pulizia a monte
    is_rosa = any(k in q for k in ["rosa", "amor", "sentiment"])
    is_bullismo = any(k in q for k in ["bullis", "bullo"])
    is_cucina = "cucin" in q or "ricett" in q
    is_noir = "noir" in q

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Strategia mirata per argomento
    if is_rosa:
        # Peschiamo DIRETTAMENTE le regine del rosa per evitare che altri autori riempiano la lista
        cursor.execute("SELECT testo_completo, testo_normalizzato FROM libri WHERE testo_normalizzato LIKE '%modignani%' OR testo_normalizzato LIKE '%steel%' OR (testo_normalizzato LIKE '%romanzo%' AND testo_normalizzato LIKE '%amor%') LIMIT 150")
    elif is_bullismo:
        cursor.execute("SELECT testo_completo, testo_normalizzato FROM libri WHERE testo_normalizzato LIKE '%bullis%' OR testo_normalizzato LIKE '%bullo%' OR testo_normalizzato LIKE '%violenza%' LIMIT 150")
    elif is_cucina:
        cursor.execute("SELECT testo_completo, testo_normalizzato FROM libri WHERE testo_normalizzato LIKE '%cucin%' OR testo_normalizzato LIKE '%ricett%' OR testo_normalizzato LIKE '%artusi%' LIMIT 150")
    elif is_noir:
        cursor.execute("SELECT testo_completo, testo_normalizzato FROM libri WHERE testo_normalizzato LIKE '%noir%' OR testo_normalizzato LIKE '%carlotto%' OR testo_normalizzato LIKE '%camilleri%' LIMIT 150")
    else:
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole_chiave]
        parametri = [f"%{p}%" for p in parole_chiave]
        cursor.execute(f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' AND '.join(condizioni)} LIMIT 100", parametri)

    righe = cursor.fetchall()
    conn.close()

    libri_ordinati = []
    for testo_completo, testo_norm in righe:
        if len(testo_completo.strip()) < 30:
            continue
        punteggio = 0
        
        # Premiamo l'attinenza reale
        if is_rosa and "modignani" in testo_norm: punteggio += 300
        if is_rosa and "steel" in testo_norm: punteggio += 300
        if is_bullismo and "bullis" in testo_norm: punteggio += 300

        for pk in parole_chiave:
            if pk in testo_norm:
                punteggio += 50

        libri_ordinati.append((punteggio, testo_completo))

    libri_ordinati.sort(key=lambda x: x[0], reverse=True)
    return [l[1] for l in libri_ordinati]

def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Mi dispiace, nessun volume corrisponde alla ricerca. Ti invitiamo a consultare il bibliotecario in sede a Siracusa per ulteriori informazioni."

    # Mandiamo all'AI solo i primi 10, i più rilevanti in assoluto
    context = "\n\n---\n\n".join([f"LIBRO:\n{b}" for b in testi_libri[:10]])
    
    prompt_completo = (
        "Sei il bibliotecario virtuale della Biblioteca Belvedere di Siracusa.\n"
        f"L'utente sta cercando: '{user_message}'\n\n"
        f"Ecco i libri pertinenti estratti dal catalogo:\n{context}\n\n"
        "ISTRUZIONI:\n"
        "1. Crea un elenco dei libri estratti che siano coerenti con la richiesta dell'utente.\n"
        "2. Usa tassativamente questo formato: * **Titolo del Libro**, Autore - Collocazione\n"
        "3. Concludi sempre dicendo che la risposta è parziale e invita l'utente a rivolgersi al bibliotecario in sede per ulteriori informazioni complete."
    )

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        response = requests.post(url, headers={"Content-Type": "application/json"}, json={"contents": [{"role": "user", "parts": [{"text": prompt_completo}]}], "generationConfig": {"temperature": 0.2}}, timeout=10)
        
        if response.status_code == 200:
            output_ai = response.json()['candidates'][0]['content']['parts'][0]['text']
            if "bibliotecario" not in output_ai.lower():
                output_ai += "\n\n_Nota: Questa risposta è parziale. Ti invitiamo a consultare il bibliotecario in sede a Siracusa per ulteriori notizie e informazioni._"
            return output_ai
    except:
        pass

    # EMERGENZA GENERALE SNELLA
    linee_emergenza = ["📚 **Biblioteca Belvedere (SBS0CB) - Risultati (Parziale)**:\n"]
    for blocco in testi_libri[:6]:
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        linee_emergenza.append(f"• {' - '.join(linee[:3])}")
    linee_emergenza.append("\n_Nota: Questa risposta è parziale. Ti invitiamo a consultare il bibliotecario in sede a Siracusa per ulteriori notizie e informazioni complete._")
    return "\n".join(linee_emergenza)

def send_telegram(chat_id, text):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except: pass

def async_process_request(chat_id, text):
    try:
        libri_trovati = cerca_nel_db(text)
        reply = ask_gemini(text, libri_trovati)
        send_telegram(chat_id, reply)
    except:
        send_telegram(chat_id, "Errore di sistema. Consultare il bibliotecario in sede.")

@app.route("/webhook_biblioteca", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json()
        if data and "message" in data:
            chat_id = data["message"]["chat"]["id"]
            text = data["message"].get("text", "").strip()
            if text == "/start":
                send_telegram(chat_id, "Benvenuto alla Biblioteca Belvedere! Scrivimi un argomento, autore o titolo per cercare nel catalogo.")
            elif chat_id and text:
                Thread(target=async_process_request, args=(chat_id, text)).start()
    except: pass
    return "OK", 200

@app.route("/setup", methods=["GET"])
def setup():
    res = inizializza_database()
    try:
        render_url = request.host_url.rstrip("/")
        resp = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": f"{render_url}/webhook_biblioteca"}, timeout=10)
        tele_res = resp.json()
    except Exception as e: tele_res = str(e)
    return jsonify({"status": res, "telegram_response": tele_res})

@app.route("/", methods=["GET"])
def home(): return "Ready.", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
