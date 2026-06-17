import os
import sqlite3
import requests
import unicodedata
import json
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

def sanifica_per_json(testo):
    """Rimuove caratteri di controllo e simboli che possono corrompere la struttura JSON"""
    if not testo:
        return ""
    testo = testo.replace('\\', '/').replace('"', "'").replace('\t', ' ')
    # Rimuove caratteri non stampabili
    return "".join(ch for ch in testo if unicodedata.category(ch)[0] != "C" or ch in '\n\r')

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
    
    is_giallo = any(g in q for g in ["giallo", "gialli", "noir", "poliziesc"])
    is_rosa = any(g in q for g in ["rosa", "amor", "sentiment"])
    is_bullismo = any(g in q for g in ["bullis", "bullo", "violenz"])
    is_cucina = "cucin" in q or "ricett" in q
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Se l'utente cerca un macro-tema, filtriamo in modo RIGIDO all'origine per evitare dati fuori target
    if is_giallo:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%giallo%' OR testo_normalizzato LIKE '%gialli%' OR testo_normalizzato LIKE '%christie%' OR testo_normalizzato LIKE '%simenon%' OR testo_normalizzato LIKE '%camilleri%' OR testo_normalizzato LIKE '%noir%' LIMIT 60")
    elif is_rosa:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%rosa%' OR testo_normalizzato LIKE '%amor%' OR testo_normalizzato LIKE '%modignani%' OR testo_normalizzato LIKE '%steel%' OR testo_normalizzato LIKE '%sparks%' LIMIT 60")
    elif is_bullismo:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%bullis%' OR testo_normalizzato LIKE '%bullo%' OR testo_normalizzato LIKE '%scuola%' OR testo_normalizzato LIKE '%adolescen%' LIMIT 60")
    elif is_cucina:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%cucin%' OR testo_normalizzato LIKE '%ricett%' OR testo_normalizzato LIKE '%artusi%' LIMIT 60")
    else:
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole]
        parametri = [f"%{p}%" for p in parole]
        if condizioni:
            cursor.execute(f"SELECT testo_completo FROM libri WHERE {' OR '.join(condizioni)} LIMIT 50", parametri)
        else:
            cursor.execute("SELECT testo_completo FROM libri LIMIT 30")
            
    righe = cursor.fetchall()
    conn.close()
    return [r[0] for r in righe]

def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Gentile utente, non ho trovato volumi corrispondenti a questa tematica nel catalogo digitale. Ti invitiamo a consultare il bibliotecario in sede a Siracusa per verificare gli scaffali fisici."

    elenco_snello = []
    for blocco in testi_libri:
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        if linee:
            # Sanifichiamo ogni singola riga prima di accumularla
            estratto = sanifica_per_json(" / ".join(linee[:3]))
            elenco_snello.append(estratto)
            
    context = "\n".join([f"- {item}" for item in elenco_snello])
    
    prompt_completo = (
        "Sei il Consulente Bibliografico ufficiale della Biblioteca Belvedere di Siracusa.\n"
        "Il tuo scopo è formulare una breve ed elegante BIBLIOGRAFIA RAGIONATA E CRITICA basandoti esclusivamente sui libri forniti nell'elenco in basso.\n\n"
        f"L'utente richiede: '{user_message}'\n\n"
        "REGOLE DI SCRITTURA:\n"
        "1. Offri un testo fluido e discorsivo. Introduci l'argomento ed elenca i libri più rilevanti estratti dalla lista.\n"
        "2. Per ogni libro consigliato estrai chiaramente Titolo, Autore e Collocazione leggendoli dai dati forniti.\n"
        "3. Formula la risposta in testo semplice o Markdown standard pulito.\n"
        "4. Includi SEMPRE alla fine una nota che indica che la risposta è parziale e invita a consultare il bibliotecario in sede a Siracusa per informazioni complete e approfondimenti."
    )

    # Costruiamo il payload in modo nativo e sicuro
    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": f"{prompt_completo}\n\nELENCO LIBRI DISPONIBILI:\n{context}"}]
        }],
        "generationConfig": {
            "temperature": 0.3
        }
    }

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=15)
        
        if response.status_code == 200:
            res_json = response.json()
            if 'candidates' in res_json and len(res_json['candidates']) > 0:
                return res_json['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        pass

    # EMERGENZA BLINDATA (Se l'API fallisce ancora, mostriamo solo cose realmente pertinenti filtrate all'origine)
    linee_emergenza = ["📚 **Biblioteca Belvedere (SBS0CB) - Risultati della ricerca**:\n", "Gentile utente, ecco i principali titoli attinenti individuati nel catalogo:\n"]
    for blocco in testi_libri[:6]:
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        linee_emergenza.append(f"• {' - '.join(linee[:2])}")
    linee_emergenza.append("\n_Nota: Questa selezione è parziale. Ti invitiamo in sede a Siracusa per consultare il bibliotecario e visionare il catalogo completo._")
    return "\n".join(linee_emergenza)

def send_telegram(chat_id, text):
    try:
        # Rimuoviamo parse_mode per evitare blocchi causati da formattazioni imperfette dell'output
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

def async_process_request(chat_id, text):
    try:
        libri_trovati = cerca_nel_db(text)
        reply = ask_gemini(text, libri_trovati)
        send_telegram(chat_id, reply)
    except:
        send_telegram(chat_id, "Servizio momentaneamente non disponibile. Il bibliotecario in sede a Siracusa rimane a disposizione per qualsiasi ricerca.")

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
