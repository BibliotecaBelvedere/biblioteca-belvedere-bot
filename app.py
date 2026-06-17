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

def estrai_essenziale_libro(testo_blocco):
    """Pulisce il blocco eliminando i dati tipografici pesanti senza rompere la struttura"""
    if not testo_blocco:
        return ""
    # Rimuove i ritorni a capo
    testo_unito = testo_blocco.replace("\n", " ").replace("\r", " ")
    testo_pulito = re.sub(r'\s+', ' ', testo_unito).strip()
    
    # Taglio morbido: togliamo le info dopo le pagine o i centimetri se presenti
    for pattern in [r'\d+\s+p\b', r';\s+\d+\s+cm', r'-\s+ISBN']:
        match = re.search(pattern, testo_pulito)
        if match:
            testo_pulito = testo_pulito[:match.start()]
            break
            
    # Sanificazione totale per i fieri nemici del formato JSON
    testo_pulito = testo_pulito.replace('\\', '/').replace('"', "'").replace('\t', ' ')
    return testo_pulito.strip()

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
        if len(pezzi_raw) <= 1:
            pezzi_raw = contenido.split("\n\n")
            
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
    is_rosa = any(g in q for g in ["rosa", "amor", "sentiment", "romant"])
    is_bullismo = any(g in q for g in ["bullis", "bullo", "violenz"])
    is_cucina = "cucin" in q or "ricett" in q
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if is_giallo:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%giallo%' OR testo_normalizzato LIKE '%gialli%' OR testo_normalizzato LIKE '%christie%' OR testo_normalizzato LIKE '%simenon%' OR testo_normalizzato LIKE '%camilleri%' OR testo_normalizzato LIKE '%noir%' OR testo_normalizzato LIKE '%adler%' LIMIT 12")
    elif is_rosa:
        cursor.execute("SELECT testo_completo FROM libri WHERE (testo_normalizzato LIKE '% romanzo %' AND testo_normalizzato LIKE '% amor %') OR testo_normalizzato LIKE '% modignani %' OR testo_normalizzato LIKE '% steel %' OR testo_normalizzato LIKE '% sparks %' OR testo_normalizzato LIKE '% romanzo rosa %' OR testo_normalizzato LIKE '% storia d amore %' LIMIT 12")
    elif is_bullismo:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%bullis%' OR testo_normalizzato LIKE '%bullo%' OR testo_normalizzato LIKE '%scuola%' OR testo_normalizzato LIKE '%adolescen%' LIMIT 12")
    elif is_cucina:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%cucin%' OR testo_normalizzato LIKE '%ricett%' OR testo_normalizzato LIKE '%artusi%' LIMIT 12")
    else:
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole]
        parametri = [f"%{p}%" for p in parole]
        if condizioni:
            cursor.execute(f"SELECT testo_completo FROM libri WHERE {' AND '.join(condizioni)} LIMIT 12", parametri)
        else:
            cursor.execute("SELECT testo_completo FROM libri LIMIT 12")
            
    righe = cursor.fetchall()
    conn.close()
    return [r[0] for r in righe]

def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Gentile utente, non ho trovato volumi corrispondenti nel catalogo digitale. Ti invitiamo a consultare il bibliotecario in sede a Siracusa."

    elenco_essenziale = []
    for blocco in testi_libri:
        riga_snella = estrai_essenziale_libro(blocco)
        if len(riga_snella) > 10:
            elenco_essenziale.append(riga_snella)
            
    elenco_essenziale = list(set(elenco_essenziale))
    context = "\n".join([f"- {item}" for item in elenco_essenziale])
    
    prompt_completo = (
        "Sei il Consulente Bibliografico ufficiale della Biblioteca Belvedere di Siracusa.\n"
        "Il tuo compito è formulare una breve ed elegante RISPOSTA DISCORSIVA E CONSULENZIALE basandoti sui libri forniti nell'elenco in basso.\n\n"
        f"L'utente richiede: '{user_message}'\n\n"
        "REGOLE DI SCRITTURA:\n"
        "1. Offri un testo fluido, accogliente e da bibliotecario. Introduci l'argomento ed elenca i libri rilevanti estratti dalla lista.\n"
        "2. Per ogni libro menzionato scrivi chiaramente Titolo, Autore e Collocazione prendendoli dai dati forniti.\n"
        "3. Se un libro è un 'intruso' (es. un romanzo ambientato in cucina invece di un ricettario), puoi comunque menzionarlo nel discorso in modo ironico o originale.\n"
        "4. Concludi SEMPRE invitando l'utente in sede a Siracusa per consultare il bibliotecario e visionare il catalogo completo."
    )

    payload = {
        "contents": [{
            "parts": [{"text": f"{prompt_completo}\n\nELENCO LIBRI DISPONIBILI:\n{context}"}]
        }],
        "generationConfig": {
            "temperature": 0.2
        }
    }

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=25)
        
        if response.status_code == 200:
            res_json = response.json()
            if 'candidates' in res_json and len(res_json['candidates']) > 0:
                testo_ia = res_json['candidates'][0]['content']['parts'][0]['text']
                if testo_ia and len(testo_ia.strip()) > 50:
                    return testo_ia
    except:
        pass

    # Ripiego di emergenza ultra-pulito se le API di Google vanno in blocco
    linee_emergenza = ["📚 **Biblioteca Belvedere (SBS0CB) - Selezione Bibliografica**:\n", "Gentile utente, ecco i principali titoli attinenti individuati nel catalogo:\n"]
    for item in elenco_essenziale[:6]:
        linee_emergenza.append(f"• {item}")
    linee_emergenza.append("\n_Nota: Questa selezione è parziale. Ti invitiamo in sede a Siracusa per consultare il bibliotecario e visionare il catalogo completo._")
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
        send_telegram(chat_id, "Servizio momentaneamente in manutenzione. Il bibliotecario in sede a Siracusa rimane a disposizione.")

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
