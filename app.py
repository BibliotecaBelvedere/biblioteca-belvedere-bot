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
        cursor.execute("DELETE FROM libri")
        
        with open(file_reale, "r", encoding="utf-8-sig", errors="ignore") as f:
            contenuto = f.read().replace("\r\n", "\n").replace("\u00a0", "\n")
            
        pezzi_raw = contenido.split("[nd]")
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

def analizza_e_cerca(query_utente):
    q = normalize(query_utente)
    parole = [w for w in q.split() if w not in STOPWORDS and len(w) >= 2]
    
    # 1. Identificazione macro-generi tematici (Richiedono l'intervento dell'IA)
    is_giallo = any(g in q for g in ["giallo", "gialli", "noir", "poliziesc", "thriller"])
    is_rosa = any(g in q for g in ["rosa", "amor", "sentiment", "romant"])
    is_bullismo = any(g in q for g in ["bullis", "bullo", "violenz", "scuola", "adolescen"])
    is_cucina = "cucin" in q or "ricett" in q or "mangiar" in q
    is_territorio = any(g in q for g in ["territorio", "sicilia", "siracusa", "locale", "tradizion"])
    
    is_tematica_generica = is_giallo or is_rosa or is_bullismo or is_cucina or is_territorio
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Esecuzione query in base al contesto
    if is_giallo:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%giallo%' OR testo_normalizzato LIKE '%gialli%' OR testo_normalizzato LIKE '%christie%' OR testo_normalizzato LIKE '%simenon%' OR testo_normalizzato LIKE '%camilleri%' OR testo_normalizzato LIKE '%noir%' OR testo_normalizzato LIKE '%adler%' LIMIT 15")
    elif is_rosa:
        cursor.execute("SELECT testo_completo FROM libri WHERE (testo_normalizzato LIKE '% romanzo %' AND testo_normalizzato LIKE '% amor %') OR testo_normalizzato LIKE '% modignani %' OR testo_normalizzato LIKE '% steel %' OR testo_normalizzato LIKE '% sparks %' OR testo_normalizzato LIKE '% romanzo rosa %' LIMIT 15")
    elif is_bullismo:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%bullis%' OR testo_normalizzato LIKE '%bullo%' OR testo_normalizzato LIKE '%scuola%' OR testo_normalizzato LIKE '%adolescen%' LIMIT 15")
    elif is_cucina:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%cucin%' OR testo_normalizzato LIKE '%ricett%' OR testo_normalizzato LIKE '%artusi%' LIMIT 15")
    elif is_territorio:
        cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE '%sicili%' OR testo_normalizzato LIKE '%siracusa%' OR testo_normalizzato LIKE '%storia locale%' LIMIT 15")
    else:
        # Ricerca specifica per autore o titolo (es: vittorini, umberto eco)
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole]
        parametri = [f"%{p}%" for p in parole]
        if condizioni:
            cursor.execute(f"SELECT testo_completo FROM libri WHERE {' AND '.join(condizioni)} LIMIT 15", parametri)
        else:
            cursor.execute("SELECT testo_completo FROM libri WHERE testo_normalizzato LIKE ? LIMIT 15", (f"%{q}%",))
            
    righe = cursor.fetchall()
    conn.close()
    
    testi_libri = [r[0] for r in righe]
    return testi_libri, is_tematica_generica

def genera_risposta_diretta(elenco_libri):
    """Genera all'istante una risposta strutturata senza passare da Gemini"""
    if not elenco_libri:
        return "Gentile utente, non ho trovato volumi corrispondenti a questa specifica ricerca nel catalogo digitale. Ti invitiamo a consultare il bibliotecario in sede a Siracusa per verificare gli scaffali cartacei."
        
    elenco_essenziale = []
    for blocco in elenco_libri:
        riga = estrai_essenziale_libro(blocco)
        if len(riga) > 10:
            elenco_essenziale.append(riga)
            
    elenco_essenziale = list(set(elenco_essenziale))
    
    testo_risposta = (
        "📚 **Biblioteca Belvedere (SBS0CB) - Risultato della Ricerca**:\n\n"
        "Gentile utente, ecco i titoli specifici individuati direttamente nel nostro catalogo:\n\n"
    )
    for libro in elenco_essenziale[:10]:
        testo_risposta += f"• {libro}\n"
        
    testo_risposta += "\n_Nota: Questa selezione è estratta direttamente dal database. Ti invitiamo in sede a Siracusa per consultare il bibliotecario e visionare le opere complete._"
    return testo_risposta

def ask_gemini(user_message, testi_libri):
    elenco_essenziale = []
    for blocco in testi_libri:
        riga_snella = estrai_essenziale_libro(blocco)
        if len(riga_snella) > 10:
            elenco_essenziale.append(riga_snella)
            
    elenco_essenziale = list(set(elenco_essenziale))
    context = "\n".join([f"- {item}" for item in elenco_essenziale])
    
    prompt_completo = (
        "Sei il Consulente Bibliografico ufficiale della Biblioteca Belvedere di Siracusa.\n"
        "Il tuo scopo è formulare una breve, colta ed elegante BIBLIOGRAFIA RAGIONATA E CRITICA basandoti sui libri forniti nell'elenco in basso.\n\n"
        f"L'utente richiede: '{user_message}'\n\n"
        "REGOLE TASSATIVE DI SCRITTURA:\n"
        "1. Offri una risposta fluida, accogliente e discorsiva. Introduci l'argomento ed elenca i libri più rilevanti estratti dalla lista.\n"
        "2. Per ogni libro consigliato estrai chiaramente Titolo, Autore e Collocazione leggendoli dai dati forniti.\n"
        "3. Formula la risposta con uno stile professionale ed editoriale. Se un libro dell'elenco ti sembra fuori tema (un intruso), ignoralo e non inserirlo nella selezione.\n"
        "4. Includi SEMPRE alla fine una nota che indica che la risposta è parziale e invita a consultare il bibliotecario in sede a Siracusa per informazioni complete e approfondimenti."
    )

    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": f"{prompt_completo}\n\nELENCO SCHEDE DISPONIBILI:\n{context}"}]
        }],
        "generationConfig": { "temperature": 0.3 },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=25)
        if response.status_code == 200:
            res_json = response.json()
            if 'candidates' in res_json and len(res_json['candidates']) > 0:
                testo_risposta = res_json['candidates'][0]['content']['parts'][0]['text']
                if testo_risposta and len(testo_risposta.strip()) > 40:
                    return testo_risposta
    except:
        pass

    return genera_risposta_diretta(testi_libri)

def send_telegram(chat_id, text):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except: pass

def async_process_request(chat_id, text):
    try:
        libri_trovati, richiede_ai = analizza_e_cerca(text)
        
        if richiede_ai and libri_trovati:
            # È una ricerca astratta/ragionata -> Chiediamo a Gemini
            reply = ask_gemini(text, libri_trovati)
        else:
            # È una ricerca secca per Autore o Titolo (es: Vittorini) -> Risposta fulminea dal database
            reply = genera_risposta_diretta(libri_trovati)
            
        send_telegram(chat_id, reply)
    except:
        send_telegram(chat_id, "Servizio di consultazione online attivo. Il bibliotecario in sede a Siracusa rimane a disposizione.")

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
