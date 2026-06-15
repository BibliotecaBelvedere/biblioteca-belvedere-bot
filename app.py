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

# PARSER INTELLIGENTE: Ricostruisce le schede del catalogo SBS0CB senza spezzarle
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
        # Sostituiamo gli spazi unificatori spazzatura con a capo puliti
        contenuto = f.read().replace("\ufeff", "").replace("\r\n", "\n").replace("\u00a0", "\n")
        
    # Dividiamo il catalogo usando come punto di riferimento l'indicatore di record del vostro software [nd]
    pezzi_raw = contenuto.split("[nd]")
    blocchi_effettivi = []
    
    for pezzo in pezzi_raw:
        linee = [l.strip() for l in pezzo.split("\n") if l.strip()]
        # Eliminiamo le righe di intestazione del file generali
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
    return f"Database ricostruito! Caricati {conteggio} libri reali e separati."

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
    
    limite_libri = 15
    mostrati_subito = testi_libri[:limite_libri]
    piu_altri = len(testi_libri) > limite_libri
    
    context_list = []
    for blocco in mostrati_subito:
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        # Mostriamo solo le prime 4 righe di ogni scheda (contengono tutto il necessario)
        blocco_corto = " | ".join(linee[:4])
        context_list.append(blocco_corto)
        
    context = "\n---\n".join(context_list)
    
    prompt_completo = (
        "Sei l'assistente della Biblioteca Belvedere di Siracusa (SBS0CB).\n"
        "Genera un elenco puntato dei libri trovati.\n"
        f"Dati estratti dal catalogo:\n{context}\n\n"
        "Per ogni libro scrivi su una sola riga in modo pulito ed elegante: **Titolo**, Autore e Collocazione.\n"
        "Evita elenchi confusionari o sotto-punti. Sii schematico.\n"
        "Al termine aggiungi la nota che invita a chiedere al bibliotecario."
    )

    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    # PARACADUTE INTEGRATO
    if not response or response.status_code != 200:
        linee_emergenza = [
            "📚 **Biblioteca Belvedere (SBS0CB) - Catalogo Risultati**:\n",
            "Ecco i volumi corrispondenti trovati nel sistema:\n"
        ]
        for blocco in mostrati_subito:
            linee = [l.strip() for l in blocco.split('\n') if l.strip()]
            info_libro = " - ".join(linee[:3])
            linee_emergenza.append(f"• {info_libro}")
            
        linee_emergenza.append("\n_Nota: Questa è una selezione dei titoli disponibili. In biblioteca potrebbero essercene altri, ti invitiamo a chiedere direttamente al bibliotecario per una ricerca completa._")
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
        return "Errore nella ricezione dei dati. Riprova."

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

@app.route("/debug_catalogo", methods=["GET"])
def debug_catalogo():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM libri")
        conteggio = cursor.fetchone()[0]
        cursor.execute("SELECT testo_completo FROM libri LIMIT 3")
        esempi = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            "stato_database": "Ricostruito con successo",
            "libri_totali_distinti_nel_db": conteggio,
            "anteprima_schede_reali": esempi
        })
    except Exception as e:
        return f"Errore: {str(e)}"

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
