import os
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

# Teniamo solo pochissime stopword strutturali, il resto lo lasciamo per dare contesto alla ricerca
STOPWORDS = {'che', 'del', 'della', 'di', 'da', 'in', 'per', 'con', 'su', 'a', 'un', 'una', 'il', 'la', 'i', 'gli', 'le', 'mi', 'ti', 'ci', 'cerca', 'cerco', 'trova'}

def normalize(s):
    s = str(s).lower().strip()
    for c in ['?', '!', ',', '.', ';', ':', '-', '_', '*', '"', "'"]:
        s = s.replace(c, ' ')
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
                blocchi_effettivi.append("\n".join(linee_pulite))

        for blocco in blocchi_effettivi:
            cursor.execute("INSERT INTO libri (testo_completo, testo_normalizzato) VALUES (?, ?)", (blocco, normalize(blocco)))
        conn.commit()
        conn.close()
        return f"SUCCESS: Caricati {len(blocchi_effettivi)} libri."
    except Exception as e:
        return f"ERRORE: {str(e)}"

def cerca_nel_db(query):
    q = normalize(query)
    parole = [w for w in q.split() if w not in STOPWORDS and len(w) >= 2]
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Se la ricerca è generica o legata a un macro-genere, estraiamo un campione molto ampio e variegato dal catalogo
    # per permettere all'IA di fare collegamenti intelligenti e pescare gli autori giusti.
    if any(g in q for g in ["giallo", "gialli", "rosa", "amor", "bullis", "bullo", "cucin", "noir", "storia"]):
        # Creiamo una mega-query che intercetta i termini cardine del genere o potenziali autori correlati nella cultura dell'IA
        cursor.execute("""
            SELECT testo_completo FROM libri 
            WHERE testo_normalizzato LIKE '%giallo%' OR testo_normalizzato LIKE '%gialli%' 
               OR testo_normalizzato LIKE '%christie%' OR testo_normalizzato LIKE '%simenon%' OR testo_normalizzato LIKE '%camilleri%'
               OR testo_normalizzato LIKE '%rosa%' OR testo_normalizzato LIKE '%amor%' OR testo_normalizzato LIKE '%modignani%' OR testo_normalizzato LIKE '%steel%'
               OR testo_normalizzato LIKE '%bullis%' OR testo_normalizzato LIKE '%bullo%' OR testo_normalizzato LIKE '%scuola%' OR testo_normalizzato LIKE '%adolescen%'
               OR testo_normalizzato LIKE '%cucin%' OR testo_normalizzato LIKE '%ricett%' OR testo_normalizzato LIKE '%artusi%'
               OR testo_normalizzato LIKE '%noir%' OR testo_normalizzato LIKE '%carlotto%' OR testo_normalizzato LIKE '%carofiglio%'
            LIMIT 250
        """)
    else:
        # Ricerca standard flessibile per parole in OR per non perdere nulla, l'IA farà la selezione di qualità
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole]
        parametri = [f"%{p}%" for p in parole]
        if condizioni:
            cursor.execute(f"SELECT testo_completo FROM libri WHERE {' OR '.join(condizioni)} LIMIT 150", parametri)
        else:
            cursor.execute("SELECT testo_completo FROM libri LIMIT 100")
            
    righe = cursor.fetchall()
    conn.close()
    return [r[0] for r in righe]

def ask_gemini(user_message, testi_libri):
    # Uniamo i testi trovati creando la base dati per l'IA
    context = "\n\n---\n\n".join([f"SCHEDA CATALOGO BIENNALE:\n{b}" for b in testi_libri])
    
    prompt_completo = (
        "Sei un Bibliotecario Esperto, colto e raffinato della Biblioteca Belvedere di Siracusa.\n"
        "Il tuo compito NON è fare una ricerca testuale stupida, ma offrire una CONSULENZA BIBLIOGRAFICA RAGIONATA E CRITICA.\n\n"
        f"L'utente ti chiede: '{user_message}'\n\n"
        "ISTRUZIONI OPERATIVE RIGIDE:\n"
        "1. Usa la tua cultura letteraria: Se l'utente ti chiede un genere (es. gialli, romanzi rosa, libri sul bullismo), analizza i dati del catalogo qui sotto, riconosci gli autori pertinenti (es. se chiede gialli, individua Georges Simenon o Agatha Christie anche se la parola 'giallo' non appare nella loro scheda) e seleziona le opere migliori presenti.\n"
        "2. Formula una vera e propria 'Bibliografia Ragionata': introduci brevemente il tema o l'autore con competenza, dopodiché presenta i libri selezionati inserendo per ciascuno un breve commento critico del perché è rilevante.\n"
        "3. Se l'utente cerca un libro specifico che NON è presente nel catalogo fornito, usa le tue conoscenze per spiegare di cosa tratta il libro cercato e proponi subito delle alternative valide e affini realmente presenti nel catalogo.\n"
        "4. Per i libri consigliati che trovi nel catalogo, mostra chiaramente il Titolo, l'Autore e la Collocazione (es. I 23b-2).\n"
        "5. Adotta un tono accogliente, professionale e colto. Concludi sempre ricordando che la risposta è parziale e che il Bibliotecario in sede a Siracusa è a disposizione per ulteriori notizie, approfondimenti e per consultare il catalogo completo.\n\n"
        f"Ecco i dati del catalogo a tua disposizione su cui lavorare:\n{context}"
    )

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        response = requests.post(url, headers={"Content-Type": "application/json"}, json={"contents": [{"role": "user", "parts": [{"text": prompt_completo}]}], "generationConfig": {"temperature": 0.4}}, timeout=15)
        
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
    except:
        pass

    return "Gentile utente, si è verificato un rallentamento nel caricamento dei dati culturali. Ti invitiamo a rivolgerti direttamente al bibliotecario in sede alla Biblioteca Belvedere di Siracusa per ricevere una bibliografia ragionata e completa sul tema richiesto."

def send_telegram(chat_id, text):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def async_process_request(chat_id, text):
    try:
        libri_trovati = cerca_nel_db(text)
        reply = ask_gemini(text, libri_trovati)
        send_telegram(chat_id, reply)
    except:
        send_telegram(chat_id, "Gentile utente, il sistema ha riscontrato un errore. Il bibliotecario in sede a Siracusa è a tua completa disposizione.")

@app.route("/webhook_biblioteca", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json()
        if data and "message" in data:
            chat_id = data["message"]["chat"]["id"]
            text = data["message"].get("text", "").strip()
            if text == "/start":
                send_telegram(chat_id, "Benvenuto al servizio di consulenza bibliografica della Biblioteca Belvedere! Chiedimi pure consigli di lettura, bibliografie tematiche o informazioni sugli autori presenti nel nostro catalogo.")
            elif chat_id and text:
                Thread(target=async_process_request, args=(chat_id, text)).start()
    except: pass
    return "OK", 200

@app.route("/setup", methods=["GET"])
def setup():
    res = inizializza_database()
    try:
        render_url = request.host_url.rstrip("/")
        requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": f"{render_url}/webhook_biblioteca"}, timeout=10)
        tele_res = "Webhook configurato correttamente."
    except Exception as e: tele_res = str(e)
    return jsonify({"status": res, "telegram_response": tele_res})

@app.route("/", methods=["GET"])
def home(): return "Consulenza Bibliografica Attiva.", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
