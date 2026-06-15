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

CATALOGO_FILE = "catalogo.txt"
DB_FILE = "catalogo.db"

# STOPWORDS potenziate per isolare solo i nomi veri
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
    'crechi','creca','su','di','da','a','in','qualcosa',
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
    # Controllo minuscole/maiuscole flessibile per Linux
    file_reale = CATALOGO_FILE
    if not os.path.exists(file_reale):
        if os.path.exists("Catalogo.txt"):
            file_reale = "Catalogo.txt"
        else:
            return "Errore: catalogo.txt assente nella cartella principale."

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
        
        # CODIFICA CORAZZATA: utf-8-sig pulisce i file Windows automaticamente, errors="ignore" evita il crash 500
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
        return f"Database ricostruito con successo con {len(blocchi_effettivi)} libri!"
    except Exception as e:
        return f"Errore tecnico durante l'inizializzazione: {str(e)}"

# MOTORE DI RICERCA IBRIDO (DB + FILTRO PYTHON)
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
        condizioni.append("testo_normalizzato LIKE ?")
        parametri.append(f"%{parola}%")
        
    if not condizioni:
        conn.close()
        return []

    sql_query = f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' AND '.join(condizioni)} LIMIT 60"
    cursor.execute(sql_query, parametri)
    righe = cursor.fetchall()
    conn.close()
    
    risultati_filtrati = []
    
    for testo_completo, testo_normalizzato in righe:
        valido = True
        for parola in parole:
            if len(parola) <= 3:
                if not re.search(rf"\b{parola}\b", testo_normalizzato):
                    valido = False
                    break
        if valido:
            risultati_filtrati.append(testo_completo)
            
    if not risultati_filtrati and len(condizioni) > 1:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        condizioni_or = ["testo_normalizzato LIKE ?" for _ in parole]
        parametri_or = [f"%{p}%" for p in parole]
        sql_query_or = f"SELECT testo_completo FROM libri WHERE {' OR '.join(condizioni_or)} LIMIT 15"
        cursor.execute(sql_query_or, parametri_or)
        risultati_filtrati = [row[0] for row in cursor.fetchall()]
        conn.close()
            
    return risultati_filtrati[:20]

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
    
    context_list = []
    for blocco in mostrati_subito:
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        blocco_corto = " | ".join(linee[:5])
        context_list.append(blocco_corto)
        
    context = "\n---\n".join(context_list)
    
    prompt_completo = (
        "Sei l'assistente ufficiale della Biblioteca Belvedere di Siracusa (SBS0CB).\n"
        "Genera un elenco puntato chiaro ed elegante dei libri trovati.\n"
        f"Dati estratti dal catalogo:\n{context}\n\n"
        "ISTRUZIONI OBBLIGATORIE:\n"
        "1. Genera l'elenco includendo SOLO i volumi strettamente pertinenti con la richiesta dell'utente.\n"
        "2. Per ogni libro valido scrivi su una singola riga: **Titolo**, Autore e Collocazione.\n"
        "3. Non inventare dati. Se mancano delle informazioni, omettile senza lasciare frasi a metà.\n"
        "4. Includi a fine messaggio l'invito istituzionale a rivolgersi al personale in sede."
    )

    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    if not response or response.status_code != 200:
        linee_emergenza = [
            "📚 **Biblioteca Belvedere (SBS0CB) - Risultati Ricerca**:\n",
            "Ecco i volumi trovati nel sistema:\n"
        ]
        for blocco in mostrati_subito:
            linee = [l.strip() for l in blocco.split('\n') if l.strip()]
            info_libro = " - ".join(linee[:3])
            linee_emergenza.append(f"• {info_libro}")
            
        linee_emergenza.append("\n_Nota: Questa è una selezione dei titoli disponibili. In biblioteca potrebbero essercene altri, ti invitiamo a chiedere direttamente al bibliotecario per una ricerca completa._")
        return "\n".join(linee_emergenza)
            
    try:
        data = response.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except:
        return "Errore nella formattazione dei dati. Riprova."

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
        send_telegram(chat_id, "Si è verificato un problema nell'elaborazione della ricerca. Riprova.")

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
    # Rimosso il caricamento automatico all'avvio per evitare crash iniziali di Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
