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

# CONVERTITORE AUTOMATICO: Legge il file di testo e crea il Database SQLite
def inizializza_database():
    if not os.path.exists(CATALOGO_FILE):
        print("ATTENZIONE: catalogo.txt non trovato. Impossibile creare il DB.")
        return

    print("Inizializzazione del database SQLite in corso...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Crea la tabella dei libri
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS libri (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            testo_completo TEXT,
            testo_normalizzato TEXT
        )
    ''')
    
    # Svuota la tabella per ricaricarla aggiornata
    cursor.execute("DELETE FROM libri")
    
    with open(CATALOGO_FILE, "r", encoding="utf-8") as f:
        contenuto = f.read().replace("\r\n", "\n")
        
    blocchi = [b.strip() for b in contenuto.split("\n\n") if b.strip()]
    
    # Se il file non è diviso da doppi a capo, lo dividiamo ogni 5 righe
    if len(blocchi) <= 1:
        righe = [r.strip() for r in contenuto.split("\n") if r.strip()]
        blocchi = ["\n".join(righe[i:i+5]) for i in range(0, len(righe), 5)]

    for blocco in blocchi:
        cursor.execute(
            "INSERT INTO libri (testo_completo, testo_normalizzato) VALUES (?, ?)",
            (blocco, normalize(blocco))
        )
        
    conn.commit()
    conn.close()
    print(f"Database sincronizzato con successo! Caricati {len(blocchi)} blocchi.")

# CERCATORE SQL: Interroga il database alla velocità della luce
def cerca_nel_db(query):
    q = normalize(query)
    parole = [w for w in q.split() if len(w) > 2 and w not in STOPWORDS]
    
    if not parole:
        return []
        
    # Gestione facilitata per la cucina
    if any(x in q for x in ["cucin", "ricett", "mangiar", "gastronom"]):
        parole.append("cucin")
        parole.append("ricett")
        parole.append("artusi")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Costruiamo la query SQL dinamica per cercare TUTTE le parole richieste
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
            timeout=25
        )
        return response
    except:
        return None

def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Mi dispiace, nessun volume nel nostro catalogo corrisponde a questa richiesta."
    
    # Se ci sono troppi libri, ne mandiamo massimo 25 a Gemini per evitare di rompere la chat
    mostrati_subito = testi_libri[:25]
    piu_altri = len(testi_libri) > 25
    
    context = "\n\n---\n\n".join(mostrati_subito)
    
    prompt_completo = (
        "Sei l'assistente virtuale ufficiale della Biblioteca Belvedere di Siracusa (SBS0CB).\n"
        "Rispondi in modo cortese, professionale e chiaro.\n\n"
        f"Ecco l'elenco dei libri estratti direttamente dal nostro database:\n{context}\n\n"
        f"Richiesta del lettore: {user_message}\n\n"
        "ISTRUZIONI RIGIDE:\n"
        "1. Genera un elenco puntato dei libri pertinenti trovati.\n"
        "2. Per ogni libro estrai dal testo e scrivi chiaramente: Titolo, Autore e Collocazione.\n"
        "3. Usa una formattazione pulita (es. Titolo in grassetto).\n"
        f"4. NOTA DI CHIUSURA: Inserisci sempre alla fine questo testo esatto: 'Nota: Al momento ti sto mostrando una selezione dei titoli disponibili nel nostro catalogo cartaceo. In biblioteca potrebbero essercene altri, ti invitiamo a chiedere direttamente al bibliotecario per una ricerca completa.'"
    )

    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    if not response or response.status_code != 200:
        time.sleep(2)
        response = call_gemini_api("gemini-1.5-flash", prompt_completo)

    if not response or response.status_code != 200:
        # SE GEMINI È INTASATO, IL DATABASE GUIDA IL BOT COMUNQUE! Generiamo una risposta automatica d'emergenza
        linee_emergenza = ["Gentile utente, ecco i risultati trovati nel catalogo:\n"]
        for libro in mostrati_subito:
            linee_emergenza.append(f"• {libro.split('\n')[0]}")
        linee_emergenza.append("\nNota: Per l'elenco completo e le collocazioni, chiedi al bibliotecario in sede.")
        return "\n".join(linee_emergenza)
            
    try:
        data = response.json()
        testo_ia = data['candidates'][0]['content']['parts'][0]['text']
        if piu_altri:
            testo_ia += f"\n\n⚠️ *Attenzione*: Ci sono altri {len(testi_libri) - 25} libri corrispondenti nel catalogo. Chiedi al bibliotecario in sede per vederli tutti!"
        return testo_ia
    except:
        return "Si è verificato un piccolo errore nella formattazione dei dati. Riprova tra un istante."

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
            send_telegram(chat_id, "Benvenuto nell'assistente della Biblioteca Belvedere! 📚 Scrivimi pure un autore, un titolo o un argomento (es. Cucina, Calvino, Sicila) per cercare i libri disponibili.")
            return "OK", 200
            
        thread = Thread(target=async_process_request, args=(chat_id, text))
        thread.start()
        
    except Exception as e:
        if 'chat_id' in locals():
            send_telegram(chat_id, f"Servizio momentaneamente in manutenzione. Riprova tra poco.")
            
    return "OK", 200

@app.route("/setup", methods=["GET"])
def setup():
    inizializza_database() # Forza la creazione/aggiornamento del database
    render_url = request.host_url.rstrip("/")
    resp = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": f"{render_url}/webhook_biblioteca"}, timeout=10)
    return jsonify({"status": "Database pronto e Webhook collegato!", "telegram_response": resp.json()})

@app.route("/", methods=["GET"])
def home():
    return "Assistente Biblioteca con Database SQLite Attivo e Pronto.", 200

if __name__ == "__main__":
    inizializza_database() # Crea il DB all'avvio dell'applicazione
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
