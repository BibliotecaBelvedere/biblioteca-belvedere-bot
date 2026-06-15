import os
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

CATALOGO_FILE = "catalogo.txt"
DB_FILE = "catalogo.db"

# STOPWORDS ultra-potenziate per lasciare solo ed esclusivamente i nomi da cercare
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
    'crechi','creca','su','di','da','a','in','qualcosa','su','per',
    'mostrami','elenco','lista','autori','autore','volumi','volume','titoli','titolo',
    'teatro','commedia','tragedia','dramma','romanzo','romanzi','saggio','saggi'
}

def normalize(s):
    s = str(s).lower().strip()
    for c in ['?', '!', ',', '.', ';', ':', '-', '_', '*', '"', "'"]:
        s = s.replace(c, ' ')
    s = s.replace('č', 'c').replace('š', 's').replace('ś', 's').replace('ā', 'a')
    s = unicodedata.normalize("NFD", s)
    return " ".join("".join(c for c in s if unicodedata.category(c) != "Mn").split())

def inizializza_database():
    if not os.path.exists(CATALOGO_FILE):
        return "Errore: catalogo.txt assente."

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
    
    with open(CATALOGO_FILE, "r", encoding="utf-8") as f:
        contenuto = f.read().replace("\ufeff", "").replace("\r\n", "\n").replace("\u00a0", "\n")
        
    pezzi_raw = contenido.split("[nd]")
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
    
    cursor.execute("SELECT COUNT(*) FROM libri")
    conteggio = cursor.fetchone()[0]
    conn.close()
    return f"Database ricostruito! Caricati {conteggio} libri."

# IL MOTORE DI RICERCA CHIRURGICO (Usa AND se ci sono più parole importanti)
def cerca_nel_db(query):
    q = normalize(query)
    parole = [w for w in q.split() if len(w) > 2 and w not in STOPWORDS]
    
    if not parole:
        return []
        
    if any(x in q for x in ["cucin", "ricett", "mangiar", "gastronom"]):
        parole = ["cucin", "ricett"]

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    condizioni = []
    parametri = []
    for parola in parole:
        if parola in ["eco", "sof", "po"]: # Gestione stringhe corte sensibili
            condizioni.append("(testo_normalizzato LIKE ? OR testo_normalizzato LIKE ? OR testo_normalizzato LIKE ?)")
            parameters_sub = [f"% {parola} %", f"{parola} %", f"% {parola}"]
            parametri.extend(parameters_sub)
        else:
            condizioni.append("testo_normalizzato LIKE ?")
            parametri.append(f"%{parola}%")
        
    if not condizioni:
        conn.close()
        return []

    # USIAMO AND: Tutti i criteri devono essere soddisfatti contemporaneamente!
    sql_query = f"SELECT testo_completo FROM libri WHERE {' AND '.join(condizioni)} LIMIT 30"
    cursor.execute(sql_query, wizard_params:=parametri)
    
    risultati = [row[0] for row in cursor.fetchall()]
    
    # SE CON 'AND' NON TROVA NULLA (magari l'utente ha scritto Sofocle Edipo ma sono schede separate)
    # Allora fa un tentativo di emergenza con 'OR' ma molto limitato
    if not risultati and len(condizioni) > 1:
        sql_query_or = f"SELECT testo_completo FROM libri WHERE {' OR '.join(condizioni)} LIMIT 15"
        cursor.execute(sql_query_or, parametri)
        risultati = [row[0] for row in cursor.fetchall()]

    conn.close()
    return risultati

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
    piu_altri = len(testi_libri) > limite_libri
    
    context_list = []
    for blocco in mostrati_subito:
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        blocco_corto = " | ".join(linee[:5])
        context_list.append(blocco_corto)
        
    context = "\n---\n".join(context_list)
    
    prompt_completo = (
        "Sei l'assistente della Biblioteca Belvedere di Siracusa (SBS0CB).\n"
        "Genera un elenco puntato chiaro ed elegante dei libri trovati.\n"
        f"Dati grezzi del catalogo:\n{context}\n\n"
        "ISTRUZIONI RIGIDE:\n"
        "1. Includi nell'elenco SOLO i libri che sono coerenti con la richiesta dell'utente. Scarta i risultati palesemente fuori tema.\n"
        "2. Per ogni libro valido scrivi su una sola riga: **Titolo**, Autore e Collocazione.\n"
        "3. Non tagliare le frasi a metà.\n"
        "4. Al termine dell'elenco aggiungi sempre la nota che invita a chiedere al bibliotecario."
    )

    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    if not response or response.status_code != 200:
        linee_emergenza = [
            "📚 **Biblioteca Belvedere (SBS0CB) - Risultati della ricerca**:\n",
            "Ecco i volumi trovati direttamente nel sistema:\n"
        ]
        for blocco in mostrati_subito:
            linee = [l.strip() for l in blocco.split('\n') if l.strip()]
            info_libro = " - ".join(linee[:3])
            linee_emergenza.append(f"• {info_libro}")
            
        linee_emergenza.append("\n_Nota: Questa è una selezione dei titoli disponibili. In biblioteca potrebbero essercene altri, ti invitiamo a chiedere direttamente al bibliotecario per una ricerca completa._")
        if piu_altri:
            linee_emergenza.append(f"\n⚠️ *Nota*: Ci sono altri libri corrispondenti nel catalogo. Chiedi in sede per vederli tutti!")
        return "\n".join(linee_emergenza)
            
    try:
        data = response.json()
        testo_ia = data['candidates'][0]['content']['parts'][0]['text']
        if piu_altri:
            testo_ia += f"\n\n⚠️ *Nota*: Ci sono altri libri corrispondenti nel catalogo. Chiedi in sede per consultarli tutti!"
        return testo_ia
    except:
        return "Errore nella formattazione dei dati di risposta."

def send_telegram(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

def async_process_request(chat_id, text):
    libri_trovati = cerca_nel_db(text)
    reply = ask_gemini(text, libri_trovati)
    send_telegram(chat_id, reply)

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
            send_telegram(chat_id, "Benvenuto nell'assistente della Biblioteca Belvedere! 📚 Scrivimi un autore o un argomento per cercare i libri nel catalogo.")
            return "OK", 200
            
        thread = Thread(target=async_process_request, args=(chat_id, text))
        thread.start()
        
    except Exception as e:
        pass
            
    return "OK", 200

@app.route("/setup", methods=["GET"])
def setup():
    res = inizializza_database()
    render_url = request.host_url.rstrip("/")
    resp = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": f"{render_url}/webhook_biblioteca"}, timeout=10)
    return jsonify({"status": res, "telegram_response": resp.json()})

@app.route("/", methods=["GET"])
def home():
    return "Assistente Biblioteca Pronto.", 200

if __name__ == "__main__":
    inizializza_database()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
