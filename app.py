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
    # Dividiamo il file usando il carattere di controllo \f (form feed / interruzione di pagina)
    # Se nel tuo file non c'è \f, si divide per righe vuote doppie '\n\n'
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
    'tratta','trattano','scritto','scritta','uscito','uscita','hai','avete'
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
    
    # Se il file è stato letto come un unico grande blocco, lo dividiamo riga per riga
    # per analizzare i singoli libri nel testo del catalogo
    righe_catalogo = []
    for blocco in BLOCCHI_LIBRI:
        righe_catalogo.extend(blocco.split('\n'))
        
    # Uniamo le righe a gruppi di 3 o 4 per ricostruire le informazioni di un libro completo
    # (Titolo, Autore, Collocazione spesso sono su righe consecutive nel file di testo)
    for i in range(len(righe_catalogo)):
        # Creiamo una "finestra" di testo di 4 righe per catturare il contesto del libro
        contesto_libro = "\n".join(righe_catalogo[i:i+4])
        contesto_n = normalize(contesto_libro)
        
        # Calcoliamo quante parole cercate dall'utente sono presenti in queste 4 righe
        score = sum(1 for term in terms if term in contesto_n)
        
        # Se troviamo una corrispondenza forte (es. c'è il titolo o l'autore)
        if score == len(terms) or (len(terms) > 1 and score >= len(terms) - 1):
            # Verifichiamo se questa corrispondenza non sia già stata salvata per evitare duplicati
            if not any(normalize(m[0][:30]) == normalize(contesto_libro[:30]) for m in matched_blocks):
                matched_blocks.append((contesto_libro, score))
            
    # Ordiniamo per punteggio di rilevanza
    matched_blocks.sort(key=lambda x: -x[1])
    return [b[0] for b in matched_blocks[:max_results]]

def ask_claude(user_message, text_results):
    if text_results:
        context = "\n\n---\n\n".join(text_results)
    else:
        context = "Nessun risultato trovato nel catalogo."

    system_prompt = (
        "Sei l'assistente della Biblioteca Belvedere di Siracusa. "
        "Rispondi in italiano, in modo cordiale e conciso.\n\n"
        f"Dati estratti dal catalogo cartaceo reale:\n{context}\n\n"
        "ISTRUZIONI RIGIDE:\n"
        "1. Se ci sono risultati nel testo sopra, mostra all'utente il titolo del libro e la sua COLLOCAZIONE ESATTA (es. I 19-1) così come appare scritta.\n"
        "2. Se il contesto dice 'Nessun risultato trovato', rispondi esattamente: 'Mi dispiace, questo volume non risulta nel catalogo della nostra sede.'\n"
        "3. NON TI INVENTARE MAI titoli, autori o collocazioni se non sono presenti nel testo fornito.\n"
        "4. Non aggiungere domande finali o saluti prolissi. La risposta deve finire subito dopo l'informazione del libro."
    )

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
    data = response.json()
    return "".join(c.get("text", "") for c in data.get("content", []))

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
    return f"Assistente Biblioteca Belvedere attivo — {len(BLOCCHI_LIBRI)} libri caricati da testo.", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
