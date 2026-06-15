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
    'mi','dai','dacci','dimmi','trovami','cercami','sono','ci','sono','adatti','alle',
    'crechi','creca'
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
        return "File catalogo.txt NON trovato su GitHub!"

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
        contenuto = f.read().replace("\r\n", "\n")
        
    blocchi = [b.strip() for b in contenuto.split("\n\n") if b.strip()]
    
    # Se il file non si divide con il doppio a capo, forziamo la divisione ogni 4 righe
    if len(blocchi) <= 5:
        righe = [r.strip() for r in contenuto.split("\n") if r.strip()]
        blocchi = ["\n".join(righe[i:i+4]) for i in range(0, len(righe), 4)]

    for blocco in blocchi:
        cursor.execute(
            "INSERT INTO libri (testo_completo, testo_normalizzato) VALUES (?, ?)",
            (blocco, normalize(blocco))
        )
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM libri")
    conteggio = cursor.fetchone()[0]
    conn.close()
    return f"Database sincronizzato. Caricati {conteggio} blocchi totali."

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
        
    sql_query = f"SELECT testo_completo FROM libri WHERE {' OR '.join(condizioni)}"
    cursor.execute(sql_query, parametri)
    
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
        return "Mi dispiace, nessun volume nel nostro catalogo corrisponde a questa richiesta."
    
    limite_libri = 12
    mostrati_subito = testi_libri[:limite_libri]
    piu_altri = len(testi_libri) > limite_libri
    
    context_list = []
    for blocco in mostrati_subito:
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        blocco_corto = " | ".join(linee[:3])
        context_list.append(blocco_corto)
        
    context = "\n---\n".join(context_list)
    
    prompt_completo = (
        "Sei l'assistente della Biblioteca Belvedere di Siracusa (SBS0CB).\n"
        "Genera un elenco puntato semplice dei libri trovati.\n"
        f"Dati:\n{context}\n\n"
        "Per ogni libro scrivi su una sola riga: **Titolo**, Autore e Collocazione.\n"
        "Al termine aggiungi la nota che invita a chiedere al bibliotecario."
    )

    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    if not response or response.status_code != 200:
        linee_emergenza = [
            "📚 **Biblioteca Belvedere (SBS0CB) - Risultati del Catalogo**:\n",
            "Ecco i volumi trovati direttamente nel nostro sistema:\n"
        ]
        for blocco in mostrati_subito:
            linee = [l.strip() for l in blocco.split('\n') if l.strip()]
            info_libro = " - ".join(linee[:3])
            linee_emergenza.append(f"• {info_libro}")
            
        linee_emergenza.append("\n_Nota: Questa è una selezione automatica dei titoli disponibili. In biblioteca potrebbero essercene altri, ti invitiamo a chiedere al bibliotecario per una ricerca completa._")
        if piu_altri:
            linee_emergenza.append(f"\n⚠️ *Nota*: Ci sono altri {len(testi_libri) - limite_libri} libri corrispondenti nel catalogo. Chiedi in sede per vederli tutti!")
        return "\n".join(linee_emergenza)
            
    try:
        data = response.json()
        testo_ia = data['candidates'][0]['content']['parts'][0]['text']
        if piu_altri:
            testo_ia += f"\n\n⚠️ *Nota*: Ci sono altri {len(testi_libri) - limite_libri} libri corrispondenti nel catalogo. Chiedi in sede per consultarli tutti!"
        return testo_ia
    except:
        return "Si è verificato un errore nella formattazione."

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
            send_telegram(chat_id, "Benvenuto nell'assistente della Biblioteca Belvedere! 📚 Scrivimi un autore o un argomento per cercare i libri.")
            return "OK", 200
            
        thread = Thread(target=async_process_request, args=(chat_id, text))
        thread.start()
        
    except Exception as e:
        pass
            
    return "OK", 200

# PAGINA DI DIAGNOSTICA: Ci dice esattamente cosa vede Python
@app.route("/debug_catalogo", methods=["GET"])
def debug_catalogo():
    try:
        if not os.path.exists(CATALOGO_FILE):
            return "Errore: File catalogo.txt non trovato sul server."
            
        with open(CATALOGO_FILE, "r", encoding="utf-8") as f:
            testo = f.read(1500) # Leggiamo solo i primi 1500 caratteri di test
            
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM libri")
        conteggio = cursor.fetchone()[0]
        
        # Vediamo i primi 3 blocchi salvati nel DB
        cursor.execute("SELECT testo_completo FROM libri LIMIT 3")
        esempi = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            "file_size_caratteri_totali": len(testo),
            "blocchi_creati_nel_db": conteggio,
            "anteprima_primi_caratteri_file": testo,
            "esempi_blocchi_db": esempi
        })
    except Exception as e:
        return f"Errore durante la diagnostica: {str(e)}"

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
