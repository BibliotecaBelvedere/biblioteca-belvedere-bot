import os
import requests
import unicodedata
from flask import Flask, request, jsonify

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

CATALOGO_FILE = "catalogo.txt"

STOPWORDS = {
    'che','del','della','delle','degli','dei','dal','dalla','dalle','dagli','dai',
    'nel','nella','nelle','negli','nei','sul','sulla','sulle','sugli','sui','per',
    'con','una','uno','gli','alla','allo','alle','agli','col','coi','tra','fra',
    'non','qui','qua','sua','suo','suoi','sue','mio','mia','miei','mie','tuo',
    'tua','tuoi','tue','questo','questa','questi','queste','quello','quella',
    'quelli','quelle','anche','come','dove','quando','mentre','essere','avere',
    'fare','dire','cerca','cerco','vorrei','voglio','cercare','trovare','libro',
    'libri','testo','testi','parli','parla','parlano','riguarda','riguardano',
    'tratta','trattano','scritto','scritta','uscito','uscita','hai','avete','trova','un'
}

def normalize(s):
    s = str(s).lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

def search_text_catalog(query, max_results=3):
    q = normalize(query)
    terms = [w for w in q.split() if len(w) > 2 and w not in STOPWORDS]
    if not terms:
        return []
    
    matched_blocks = []
    
    if not os.path.exists(CATALOGO_FILE):
        return []
        
    try:
        # Apriamo con utf-8 per tollerare Čehov, Šiškin, Śāntideva ecc.
        with open(CATALOGO_FILE, "r", encoding="utf-8") as f:
            contenuto = f.read()
            # Dividiamo il catalogo per blocchi reali separati da righe vuote
            blocchi = [b.strip() for b in contenuto.split("\n\n") if b.strip()]
            
        for blocco in blocchi:
            blocco_n = normalize(blocco)
            score = sum(1 for term in terms if term in blocco_n)
            if score > 0:
                matched_blocks.append((blocco, score))
                    
        matched_blocks.sort(key=lambda x: -x[1])
        return [b[0] for b in matched_blocks[:max_results]]
    except:
        return []

def ask_claude(user_message, text_results):
    if not text_results:
        return "Mi dispiace, questo volume non risulta nel catalogo della nostra sede."

    context = "\n\n---\n\n".join(text_results)
    system_prompt = (
        "Sei l'assistente della Biblioteca Belvedere di Siracusa. Rispondi in modo cordiale e conciso.\n\n"
        f"Dati del catalogo:\n{context}\n\n"
        "ISTRUZIONI:\n"
        "Mostra il titolo del libro e la sua COLLOCAZIONE ESATTA presa dai dati sopra. "
        "Non inventare nulla. Chiudi la risposta subito dopo aver fornito il libro."
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
                "max_tokens": 300,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=12,
        )
        return "".join(c.get("text", "") for c in response.json().get("content", []))
    except:
        return "Errore temporaneo di comunicazione con l'IA."

def send_telegram(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

@app.route("/webhook_biblioteca", methods=["POST"])
def telegram_webhook():
    data = request.get_json()
    if not data or "message" not in data:
        return "OK", 200
        
    message = data["message"]
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    
    if not chat_id or not text:
        return "OK", 200
        
    if text == "/start":
        send_telegram(chat_id, "Ciao! Sono l'assistente della Biblioteca Belvedere 📚 Scrivimi il titolo di un libro o un autore per cercarlo nel catalogo.")
        return "OK", 200
        
    results = search_text_catalog(text)
    reply = ask_claude(text, results)
    send_telegram(chat_id, reply)
    return "OK", 200

@app.route("/setup", methods=["GET"])
def setup():
    render_url = request.host_url.rstrip("/")
    resp = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": f"{render_url}/webhook_biblioteca"}, timeout=10)
    return jsonify(resp.json())

@app.route("/", methods=["GET"])
def home():
    return "Assistente Biblioteca Pronto.", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
