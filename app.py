import os
import requests
import unicodedata
from flask import Flask, request, jsonify

app = Flask(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
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
    'tratta','trattano','scritto','scritta','uscito','uscita','hai','avete','trova','un',
    'mi','dai','dacci','dimmi','trovami','cercami','sono','libri'
}

def normalize(s):
    s = str(s).lower().strip()
    s = s.replace('č', 'c').replace('š', 's').replace('ś', 's').replace('ā', 'a')
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

def search_text_catalog(query, max_results=3):
    q = normalize(query)
    terms = [w for w in q.split() if len(w) > 2 and w not in STOPWORDS]
    
    if not terms:
        return []
    
    if not os.path.exists(CATALOGO_FILE):
        return ["ERRORE TECNICO: File catalogo.txt non trovato."]
        
    try:
        with open(CATALOGO_FILE, "r", encoding="utf-8") as f:
            contenuto = f.read()
        
        contenuto_pulito = contenuto.replace("\r\n", "\n")
        blocchi = [b.strip() for b in contenuto_pulito.split("\n\n") if b.strip()]
        
        if len(blocchi) <= 1:
            righe = [r.strip() for r in contenuto_pulito.split("\n") if r.strip()]
            blocchi = []
            for i in range(0, len(righe), 4):
                gruppo = "\n".join(righe[i:i+6])
                blocchi.append(gruppo)
                
        matched_blocks = []
        for blocco in blocchi:
            blocco_n = normalize(blocco)
            score = sum(1 for term in terms if term in blocco_n)
            if score > 0:
                matched_blocks.append((blocco, score))
                    
        matched_blocks.sort(key=lambda x: -x[1])
        return [b[0] for b in matched_blocks[:max_results]]
    except Exception as e:
        return [f"ERRORE LETTURA: {str(e)}"]

def ask_gemini(user_message, text_results):
    if not text_results:
        return "Mi dispiace, questo volume non risulta nel catalogo della nostra sede."
        
    if "ERRORE" in text_results[0]:
        return text_results[0]

    context = "\n\n---\n\n".join(text_results)
    
    prompt_completo = (
        "Sei l'assistente della Biblioteca Belvedere di Siracusa. Rispondi in modo cordiale, formale e conciso.\n\n"
        f"Dati del catalogo estratti:\n{context}\n\n"
        f"Richiesta dell'utente: {user_message}\n\n"
        "ISTRUZIONI:\n"
        "Elenca i libri trovati indicando Titolo, Autore e la COLLOCAZIONE ESATTA.\n"
        "Se la collocazione contiene codici come '21-0', 'I 13-1', 'I 2 2', mostrala chiaramente.\n"
        "Non inventare informazioni non presenti nel testo fornito."
    )

    try:
        # URL CORRETTO: Inclusa la versione v1beta corretta per i modelli di generazione di contenuto stabili
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt_completo}]}]},
            timeout=12
        )
        if response.status_code != 200:
            return f"Errore di risposta da Gemini (Stato {response.status_code})"
            
        data = response.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return f"Errore connessione IA Google: {str(e)}"

def send_telegram(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

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
            send_telegram(chat_id, "Ciao! Sono l'assistente della Biblioteca Belvedere 📚 Scrivimi il titolo di un libro o un autore per cercarlo nel catalogo.")
            return "OK", 200
            
        results = search_text_catalog(text)
        reply = ask_gemini(text, results)
        send_telegram(chat_id, reply)
    except Exception as general_error:
        if 'chat_id' in locals():
            send_telegram(chat_id, f"Errore imprevisto: {str(general_error)}")
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
