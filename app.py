import os
import sqlite3
import requests
import unicodedata
import re
from flask import Flask, request, jsonify
from threading import Thread

app = Flask(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

DB_FILE = "catalogo.db"

STOPWORDS = {'che', 'del', 'della', 'di', 'da', 'in', 'per', 'con', 'su', 'a', 'un', 'una', 'il', 'la', 'i', 'gli', 'le', 'mi', 'ti', 'ci', 'cerca', 'cerco', 'trova'}

def normalize(s):
    s = str(s).lower().strip()
    for c in ['?', '!', ',', '.', ';', ':', '-', '_', '*', '"', "'"]:
        s = s.replace(c, ' ')
    s = unicodedata.normalize("NFD", s)
    return " ".join("".join(c for c in s if unicodedata.category(c) != "Mn").split())

def pulisci_blocco_completo(testo):
    """Scompatta il blocco eliminando ritorni a capo continui e spazzatura strutturale"""
    if not testo:
        return ""
    # Sostituisce i ritorni a capo con uno spazio per unire le righe spezzate
    testo_unito = testo.replace("\n", " ").replace("\r", " ")
    # Rimuove spazi multipli
    testo_pulito = re.sub(r'\s+', ' ', testo_unito).strip()
    # Sanificazione caratteri molesti per il JSON
    testo_pulito = testo_pulito.replace('\\', '/').replace('"', "'")
    return testo_pulito

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
            
        # Proviamo a dividere sia per [nd] sia per righe vuote doppie se [nd] fallisce
        pezzi_raw = contenuto.split("[nd]")
        if len(pezzi_raw) <= 1:
            pezzi_raw = contenuto.split("\n\n")
            
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
    
    is_giallo = any(g in q for g in ["giallo", "gialli", "noir", "poliziesc", "thriller"])
    is_rosa = any(g in q for g in ["rosa", "amor", "sentiment"])
    is_bullismo = any(g in q for g in ["bullis", "bullo", "violenz"])
    is_cucina = "cucin" in q or "ricett" in q
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Riduciamo il LIMIT a 30 ma prendiamo blocchi più ricchi di informazioni
    if is_giallo:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%giallo%' OR testo_normalizzato LIKE '%gialli%' OR testo_normalizzato LIKE '%christie%' OR testo_normalizzato LIKE '%simenon%' OR testo_normalizzato LIKE '%camilleri%' OR testo_normalizzato LIKE '%noir%' OR testo_normalizzato LIKE '%adler%' LIMIT 30")
    elif is_rosa:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%rosa%' OR testo_normalizzato LIKE '%amor%' OR testo_normalizzato LIKE '%modignani%' OR testo_normalizzato LIKE '%steel%' LIMIT 30")
    elif is_bullismo:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%bullis%' OR testo_normalizzato LIKE '%bullo%' OR testo_normalizzato LIKE '%scuola%' OR testo_normalizzato LIKE '%adolescen%' LIMIT 30")
    elif is_cucina:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%cucin%' OR testo_normalizzato LIKE '%ricett%' LIMIT 30")
    else:
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole]
        parametri = [f"%{p}%" for p in parole]
        if condizioni:
            cursor.execute(f"SELECT testo_completo FROM libri WHERE {' OR '.join(condizioni)} LIMIT 30", parametri)
        else:
            cursor.execute("SELECT testo_completo FROM libri LIMIT 20")
            
    righe = cursor.fetchall()
    conn.close()
    return [r[0] for r in righe]

def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Gentile utente, non ho trovato volumi corrispondenti nel catalogo digitale. Ti invitiamo a consultare il bibliotecario in sede a Siracusa per verificare gli scaffali fisici."

    elenco_pulito = []
    for blocco in testi_libri:
        blocco_sano = pulisci_blocco_completo(blocco)
        if len(blocco_sano) > 10:
            elenco_pulito.append(blocco_sano)
            
    # Rimuoviamo i duplicati identici estratti dal DB per non confondere l'IA
    elenco_pulito = list(set(elenco_pulito))
    context = "\n".join([f"- {item}" for item in elenco_pulito])
    
    prompt_completo = (
        "Sei il Consulente Bibliografico ufficiale della Biblioteca Belvedere di Siracusa.\n"
        "Il tuo scopo è formulare una breve ed elegante BIBLIOGRAFIA RAGIONATA E CRITICA basandoti esclusivamente sui libri forniti nell'elenco in basso.\n\n"
        f"L'utente richiede: '{user_message}'\n\n"
        "REGOLE TASSATIVE DI SCRITTURA:\n"
        "1. Offri un testo fluido, accogliente e discorsivo. Introduci l'argomento ed elenca i libri più rilevanti estratti dalla lista.\n"
        "2. IMPORTANTE: Leggi attentamente ogni riga fornita per trovare il Titolo del libro, l'Autore e la Collocazione (es. 21a-1 o I 5-1). Non inventare titoli.\n"
        "3. Se nella riga vedi solo l'autore e manca il titolo, usa la tua conoscenza enciclopedica per dedurre quale possa essere il titolo del libro partendo dalla collocazione o dalle info presenti, oppure ometti quel record se è totalmente illeggibile.\n"
        "4. Includi SEMPRE alla fine una nota che indica che la risposta è parziale e invita a consultare il bibliotecario in sede a Siracusa per informazioni complete e approfondimenti."
    )

    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": f"{prompt_completo}\n\nELENCO SCHEDE DISPONIBILI:\n{context}"}]
        }],
        "generationConfig": {
            "temperature": 0.3
        }
    }

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        # Portiamo il timeout a 25 secondi per evitare qualsiasi interruzione
        response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=25)
        
        if response.status_code == 200:
            res_json = response.json()
            if 'candidates' in res_json and len(res_json['candidates']) > 0:
                return res_json['candidates'][0]['content']['parts'][0]['text']
    except:
        pass

    # EMERGENZA TRASPARENTE (Se fallisce, mostra l'intera riga pulita del catalogo, così vedrai comunque l'intero testo)
    linee_emergenza = ["📚 **Biblioteca Belvedere (SBS0CB) - Risultati della ricerca**:\n", "Gentile utente, ecco i principali dati attinenti individuati nel catalogo:\n"]
    for item in elenco_pulito[:6]:
        # Taglia la stringa se è troppo lunga per Telegram, ma mostra abbastanza testo per vedere il titolo
        linee_emergenza.append(f"• {item[:140]}...")
    linee_emergenza.append("\n_Nota: Questa selezione è parziale. Ti invitiamo in sede a Siracusa per consultare il bibliotecario e visionare il catalogo completo._")
    return "\n".join(linee_emergenza)

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
        send_telegram(chat_id, "Servizio momentaneamente in manutenzione. Il bibliotecario in sede a Siracusa rimane a disposizione per qualsiasi ricerca.")

@app.route("/webhook_biblioteca", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json()
        if data and "message" in data:
            chat_id = data["message"]["chat"]["id"]
            text = data["message"].get("text", "").strip()
            if text == "/start":
                send_telegram(chat_id, "Benvenuto alla Biblioteca Belvedere! Chiedimi pure consigli di lettura o percorsi tematici sul nostro catalogo.")
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
        tele_res = "Webhook registrato con successo."
    except Exception as e: tele_res = str(e)
    return jsonify({"status": res, "telegram_response": tele_res})

@app.route("/", methods=["GET"])
def home(): return "Online.", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
