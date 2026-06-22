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

STOPWORDS = {'che', 'del', 'della', 'di', 'da', 'in', 'per', 'con', 'su', 'a', 'un', 'una', 'il', 'la', 'i', 'gli', 'le', 'mi', 'ti', 'ci', 'cerca', 'cerco', 'trova', 'dai', 'libri', 'sul', 'sui', 'cerchi'}

NOTABENE_INFO = (
    "\n\n_Nota: La consultazione online offre una panoramica parziale. Ti invitiamo a recarti presso la "
    "Biblioteca Belvedere di Siracusa in piazza Eurialo 18, aperta dal lunedì al venerdì dalle 8.30 alle 13.15 "
    "e il martedì e giovedì anche nel pomeriggio dalle 15.00 alle 17.15, per consultare il bibliotecario e visionare le opere complete._"
)

def normalize(s):
    s = str(s).lower().strip()
    for c in ['?', '!', ',', '.', ';', ':', '-', '_', '*', '"', "'"]:
        s = s.replace(c, ' ')
    s = unicodedata.normalize("NFD", s)
    return " ".join("".join(c for c in s if unicodedata.category(c) != "Mn").split())

def estrai_essenziale_libro(testo_blocco):
    if not testo_blocco:
        return ""
    testo_unito = testo_blocco.replace("\n", " ").replace("\r", " ")
    testo_pulito = re.sub(r'\s+', ' ', testo_unito).strip()
    
    testo_pulito = re.split(r'\d+\s+p\b', testo_pulito)[0]
    testo_pulito = re.split(r'-\s+ISBN\b', testo_pulito)[0]
    testo_pulito = re.split(r';\s+\d+\s+cm', testo_pulito)[0]
    
    testo_pulito = testo_pulito.strip()
    testo_pulito = re.sub(r'[\s\.\,\-\:\/]+$', '', testo_pulito)
    testo_pulito = testo_pulito.replace(' - . -', '').replace('\\', '/').replace('"', "'")
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
        cursor.execute('CREATE TABLE IF NOT EXISTS stati_utente (chat_id TEXT PRIMARY KEY, ultima_query TEXT)')
        cursor.execute("DELETE FROM libri")
        conn.commit()
        
        with open(file_reale, "r", encoding="utf-8-sig", errors="ignore") as f:
            contenuto_txt = f.read().replace("\r\n", "\n").replace("\u00a0", "\n")
            
        pezzi_raw = contenido_txt.split("[nd]")
        if len(pezzi_raw) <= 1:
            pezzi_raw = contenuto_txt.split("\n\n")
            
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

def imposta_stato(chat_id, query_testo):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO stati_utente (chat_id, ultima_query) VALUES (?, ?)", (str(chat_id), query_testo))
    conn.commit()
    conn.close()

def leggi_e_cancella_stato(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT ultima_query FROM stati_utente WHERE chat_id = ?", (str(chat_id),))
    riga = cursor.fetchone()
    if riga:
        cursor.execute("DELETE FROM stati_utente WHERE chat_id = ?", (str(chat_id),))
        conn.commit()
        conn.close()
        return riga[0]
    conn.close()
    return None

def cerca_diretta_catalogo(query_utente):
    q = normalize(query_utente)
    parole = [w for w in q.split() if w not in STOPWORDS and len(w) >= 2]
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if parole:
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole]
        parametri = [f"%{p}%" for p in parole]
        parametri_completi = list(parametri) + list(parametri)
        ordinamento = " + ".join([f"(CASE WHEN testo_normalizzato LIKE ? THEN 0 ELSE 1 END)" for _ in parole])
        
        query_sql = f"SELECT testo_completo FROM libri WHERE {' AND '.join(condizioni)} ORDER BY {ordinamento}, id ASC LIMIT 15"
        cursor.execute(query_sql, parametri_completi)
    else:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE ? ORDER BY (CASE WHEN testo_normalizzato LIKE ? THEN 0 ELSE 1 END), id ASC LIMIT 15", (f"%{q}%", f"{q}%"))
        
    righe = cursor.fetchall()
    conn.close()
    return [r[0] for r in righe]

def cerca_espansa_per_gemini(query_utente):
    """
    Scrematura semantica preliminare flessibile basata sul macro-contesto della richiesta.
    Estrae al massimo 20-25 record per evitare blocchi e timeout delle API.
    """
    q = normalize(query_utente)
    
    is_giallo = any(g in q for g in ["giallo", "gialli", "noir", "poliziesc", "thriller", "assass", "delitt"])
    is_rosa = any(g in q for g in ["rosa", "amor", "sentiment", "romant", "relazion", "bacio", "passion"])
    is_bullismo = any(g in q for g in ["bullis", "bullo", "prevarica", "violenz"])
    is_cucina = any(g in q for g in ["cucin", "ricett", "mangiar", "gastronom"])
    is_territorio = any(g in q for g in ["territorio", "sicilia", "siracusa", "locale", "tradizion"])
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if is_giallo:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%giallo%' OR testo_normalizzato LIKE '%gialli%' OR testo_normalizzato LIKE '%noir%' OR testo_normalizzato LIKE '%thriller%' OR testo_normalizzato LIKE '%delitto%' LIMIT 25")
    elif is_rosa:
        # Pescaggio ampio basato su concetti sfumati del genere romance, senza vincoli di autore fisso
        cursor.execute("""
            SELECT testo_completo FROM libri WHERE 
            testo_normalizzato LIKE '%romanzo%' AND (
                testo_normalizzato LIKE '%amor%' OR 
                testo_normalizzato LIKE '%rosa%' OR 
                testo_normalizzato LIKE '%bacio%' OR 
                testo_normalizzato LIKE '%sentiment%' OR 
                testo_normalizzato LIKE '%relazion%' OR
                testo_normalizzato LIKE '%amante%'
            ) LIMIT 25
        """)
    elif is_bullismo:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%bullis%' OR testo_normalizzato LIKE '%bullo%' OR testo_normalizzato LIKE '%adolescen%' LIMIT 20")
    elif is_cucina:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%cucin%' OR testo_normalizzato LIKE '%ricett%' LIMIT 20")
    elif is_territorio:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%sicili%' OR testo_normalizzato LIKE '%siracusa%' LIMIT 20")
    else:
        parole = [w for w in q.split() if w not in STOPWORDS and len(w) >= 2]
        if parole:
            condizioni = ["testo_normalizzato LIKE ?" for _ in parole]
            parametri = [f"%{p}%" for p in parole]
            cursor.execute(f"SELECT testo_completo FROM libri WHERE {' OR '.join(condizioni)} LIMIT 25", parametri)
        else:
            cursor.execute("SELECT testo_completo FROM libri ORDER BY id DESC LIMIT 20")
            
    righe = cursor.fetchall()
    conn.close()
    return [r[0] for r in righe]

def genera_risposta_diretta(elenco_libri):
    if not elenco_libri:
        return (
            "Gentile utente, non ho trovato volumi corrispondenti a questa specifica ricerca nel catalogo digitale."
            + NOTABENE_INFO
        )
        
    elenco_essenziale = []
    for blocco in elenco_libri:
        riga = estrai_essenziale_libro(blocco)
        if len(riga) > 10:
            elenco_essenziale.append(riga)
            
    elenco_essenziale = list(set(elenco_essenziale))
    
    testo_risposta = (
        "📚 **Biblioteca Belvedere (SBS0CB) - Risultato del Catalogo**:\n\n"
        "Ecco i titoli specifici individuati direttamente nei nostri registri:\n\n"
    )
    for libro in elenco_essenziale[:10]:
        testo_risposta += f"• {libro}\n"
        
    testo_risposta += NOTABENE_INFO
    return testo_risposta

def ask_gemini(chat_id, user_message, testi_libri):
    if not testi_libri:
        return "Gentile utente, non ho trovato materiale sufficiente nel catalogo per elaborare una bibliografia tematica." + NOTABENE_INFO

    elenco_essenziale = []
    for blocco in testi_libri:
        riga_snella = estrai_essenziale_libro(blocco)
        if len(riga_snella) > 10:
            elenco_essenziale.append(riga_snella)
            
    elenco_essenziale = list(set(elenco_essenziale))
    context = "\n".join([f"- {item}" for item in elenco_essenziale])
    
    prompt_completo = (
        "Sei il Consulente Bibliografico ufficiale della Biblioteca Belvedere di Siracusa.\n"
        "Analizza l'elenco di libri allegato e componi una BIBLIOGRAFIA TEMATICA RAGIONATA rispondendo alla richiesta dell'utente.\n\n"
        f"Richiesta: '{user_message}'\n\n"
        "REGOLE DI ELABORAZIONE SEMANTICA:\n"
        "1. Usa il tuo discernimento probabilistico: inserisci solo i libri che sono VERAMENTE coerenti con la richiesta (es. romanzi rosa/storie d'amore). Scarta saggi, gialli o libri estranei anche se contengono frammenti di parole simili.\n"
        "2. Seleziona un massimo di 5 titoli tra quelli forniti.\n"
        "3. Scrivi un'introduzione accogliente e descrivi ogni libro in modo accattivante, indicando Titolo, Autore ed eventuale Collocazione.\n"
        "4. Non firmarti e non inserire orari alla fine."
    )

    payload = {
        "contents": [{
            "parts": [{"text": f"{prompt_completo}\n\nLIBRI DISPONIBILI:\n{context}"}]
        }],
        "generationConfig": {
            "temperature": 0.3
        }
    }

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        # Aggiunto un timeout robusto ma contenuto a 15 secondi per preservare il ciclo vitale del server Flask
        response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=15)
        
        if response.status_code == 200:
            res_json = response.json()
            if 'candidates' in res_json and len(res_json['candidates']) > 0:
                testo_risposta = res_json['candidates'][0]['content']['parts'][0]['text']
                if testo_risposta and len(testo_risposta.strip()) > 30:
                    return testo_risposta + NOTABENE_INFO
        else:
            requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": f"⚠️ Nota di debug: Errore API {response.status_code}"})
    except Exception as e:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": f"⚠️ Nota di debug errore connessione: {str(e)}"})

    return genera_risposta_diretta(testi_libri)

def send_telegram_with_buttons(chat_id, text):
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔍 Ricerca per Autore o Titolo", "callback_data": "MODE_DIRETTA"}],
            [{"text": "📚 Ricerca per Genere o Argomento", "callback_data": "MODE_SEMANTICA"}]
        ]
    }
    payload = {
        "chat_id": chat_id,
        "text": f"Ho ricevuto la tua richiesta per: *\"{text}\"*\n\nPer favore, specifica come desideri effettuare la ricerca:",
        "parse_mode": "Markdown",
        "reply_markup": keyboard
    }
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)
    except: pass

def send_telegram(chat_id, text):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except: pass

def esegui_bivio_ricerca(chat_id, modalita, query_originale):
    if modalita == "MODE_DIRETTA":
        libri = cerca_diretta_catalogo(query_originale)
        risposta = genera_risposta_diretta(libri)
        send_telegram(chat_id, risposta)
    elif modalita == "MODE_SEMANTICA":
        send_telegram(chat_id, "🔄 Sto elaborando una bibliografia tematica con l'ausilio dell'Intelligenza Artificiale...")
        libri = cerca_espansa_per_gemini(query_originale)
        risposta = ask_gemini(chat_id, query_originale, libri)
        send_telegram(chat_id, risposta)

@app.route("/webhook_biblioteca", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json()
        if not data:
            return "OK", 200
            
        if "callback_query" in data:
            chat_id = data["callback_query"]["message"]["chat"]["id"]
            modalita = data["callback_query"]["data"]
            query_originale = leggi_e_cancella_stato(chat_id)
            
            try:
                requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": data["callback_query"]["id"]}, timeout=5)
            except: pass
            
            if query_originale:
                Thread(target=esegui_bivio_ricerca, args=(chat_id, modalita, query_originale)).start()
            else:
                send_telegram(chat_id, "Sessione scaduta. Ripeti la ricerca.")
            return "OK", 200

        if "message" in data:
            chat_id = data["message"]["chat"]["id"]
            text = data["message"].get("text", "").strip()
            
            if text == "/start":
                send_telegram(chat_id, "Benvenuto alla Biblioteca Belvedere! Mandami pure la tua richiesta.")
            elif chat_id and text:
                imposta_stato(chat_id, text)
                send_telegram_with_buttons(chat_id, text)
    except: 
        pass
    return "OK", 200

@app.route("/setup", methods=["GET"])
def setup():
    res = inizializza_database()
    try:
        render_url = request.host_url.rstrip("/")
        requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": f"{render_url}/webhook_biblioteca"}, timeout=10)
        tele_res = "Webhook configurato."
    except Exception as e: tele_res = str(e)
    return jsonify({"status": res, "telegram_response": tele_res})

@app.route("/", methods=["GET"])
def home(): return "Online.", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
