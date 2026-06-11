import os
import unicodedata
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Carica il catalogo di testo se presente
CATALOGO_FILE = "catalogo.txt"
BLOCCHI_LIBRI = []

if os.path.exists(CATALOGO_FILE):
    with open(CATALOGO_FILE, "r", encoding="utf-8") as f:
        contenuto = f.read()
    
    # Dividiamo il file provando prima con \f e poi con le righe vuote
    if "\f" in contenuto:
        BLOCCHI_LIBRI = [blocco.strip() for blocco in contenuto.split("\f") if blocco.strip()]
    else:
        BLOCCHI_LIBRI = [blocco.strip() for blocco in contenuto.split("\n\n") if blocco.strip()]
    print(f"Catalogo di testo caricato: {len(BLOCCHI_LIBRI)} blocchi di libri.")
else:
    print(f"ATTENZIONE: File {CATALOGO_FILE} non trovato!")

STOPWORDS = {
    'che','del','della','delle','degli','dei','dal','dalla','dalle','dagli','dai',
    'nel','nella','nelle','negli','nei','sul','sulla','sulle','sugli','sui','per',
    'con','una','uno','gli','alla','allo','alle','agli','col','coi','tra','fra',
    'non','qui','qua','sua','suo','suoi','sue','mio','mia','miei','mie','tuo',
    'tua','tuoi','tue','questo','questa','questi','queste','quello','quella',
    'quelli','quelle','anche','come','dove','quando','mentre','essere','avere',
    'fare','dire','cerca','cerco','vorrei','voglio','cercare','trovare','libro',
    'libri','testo','testi','parli','parla','parlano','riguarda','riguardano',
    'tratta','trattano','scritto','scritta','uscito','uscita','hai','avete',
    'avete','trova','trovami','sapere','se','c\'e','un'
}

def normalize(s):
    s = str(s).lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

def search_text_catalog(query, max_results=5):
    q = normalize(query)
    terms = [w for w in q.split() if len(w) > 2 and w not in STOPWORDS]
    if not terms:
        return []
    
    matched_blocks = []
    
    # Estraiamo tutte le singole righe del catalogo per sicurezza
    righe_catalogo = []
    for blocco in BLOCCHI_LIBRI:
        righe_catalogo.extend(blocco.split('\n'))
        
    # Scansione con finestra mobile per non perdere nessuna collocazione vicina
    for i in range(len(righe_catalogo)):
        contesto_libro = "\n".join(righe_catalogo[i:i+4])
        contesto_n = normalize(contesto_libro)
        
        # Calcoliamo quante parole cercate corrispondono nel testo
        score = sum(1 for term in terms if term in contesto_n)
        
        # Logica più morbida: basta che matchi almeno un termine significativo importante!
        if score > 0:
            # Evitiamo doppioni identici nei risultati
            if not any(normalize(m[0][:40]) == normalize(contesto_libro[:40]) for m in matched_blocks):
                matched_blocks.append((contesto_libro, score))
            
    # Ordiniamo i libri trovati dal più rilevante al meno rilevante
    matched_blocks.sort(key=lambda x: -x[1])
    return [b[0] for b in matched_blocks[:max_results]]

def ask_claude(user_message, text_results):
    if text_results:
        context = "\n\n---\n\n".join(text_results)
    else:
        # Se la ricerca fallisce sul serio, forziamo un testo chiaro per Claude
        return "Mi dispiace, questo volume non risulta nel catalogo della nostra sede."

    system_prompt = (
        "Sei l'assistente della Biblioteca Belvedere di Siracusa. "
        "Rispondi in italiano, in modo cordiale e conciso.\n\n"
        f"Dati estratti dal catalogo cartaceo reale:\n{context}\n\n"
        "ISTRUZIONI RIGIDE:\n"
        "1. Mostra all'utente il titolo del libro e la sua COLLOCAZIONE ESATTA (es. I 19-1) prendendola dal testo sopra.\n"
        "2. NON inventare titoli o codici di collocazione che non vedi scritti nei dati forniti.\n"
        "3. La risposta deve finire subito dopo l'informazione del libro, senza saluti finali prolissi."
    )

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-3-haiku-20240307",
                "max_tokens": 400,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=15,
        )
        
        # Gestione errori di autenticazione o chiavi errate
        if response.status_code != 200:
            return f"Errore di connessione con l'intelligenza artificiale (Codice {response.status_code}). Verifica la tua ANTHROPIC_API_KEY su Render."
            
        data = response.json()
        return "".join(c.get("text", "") for c in data.get("content", []))
        
    except Exception as e:
        return f"Si è verificato un piccolo ritardo o errore tecnico nell'elaborazione: {str(e)}"

def send_telegram(chat_id, text):
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for chunk in chunks:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": chunk},
            timeout=10,
        )

def set_webhook(url):
    resp = requests.post(
        f"{TELEGRAM_API}/setWebhook",
        json={"url": f"{url}/telegram"},
        timeout=10,
    )
    return resp.json()

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json()
    if not data:
        return "OK", 200
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    if not chat_id or not text:
        return "OK", 200
        
    if text == "/start":
        send_telegram(chat_id,
            "Ciao! Sono l'assistente della Biblioteca Belvedere di Siracusa 📚\n\n"
            "Scrivimi il titolo, l'autore o l'argomento che cerchi e ti aiuto a trovare i libri nel nostro catalogo!"
        )
        return "OK", 200
    
    results = search_text_catalog(text)
    reply = ask_claude(text, results)
    send_telegram(chat_id, reply)
    return "OK", 200

@app.route("/setup", methods=["GET"])
def setup():
    render_url = request.host_url.rstrip("/")
    result = set_webhook(render_url)
    return jsonify(result)

@app.route("/", methods=["GET"])
def home():
    return f"Assistente Biblioteca Belvedere attivo — {len(BLOCCHI_LIBRI)} elementi pronti nel database di testo.", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
