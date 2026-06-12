import os
import time
import requests
import unicodedata
from flask import Flask, request, jsonify
from threading import Thread

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
    'mi','dai','dacci','dimmi','trovami','cercami','sono','ci','sono','adatti','alle',
    'crechi','creca'
}

def normalize(s):
    s = str(s).lower().strip()
    for c in ['?', '!', ',', '.', ';', ':', '-', '_']:
        s = s.replace(c, ' ')
    s = s.replace('č', 'c').replace('š', 's').replace('ś', 's').replace('ā', 'a')
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

def search_text_catalog(query, max_results=12):
    q = normalize(query)
    terms = [w for w in q.split() if len(w) > 2 and w not in STOPWORDS]
    
    if not terms:
        return []
    
    if not os.path.exists(CATALOGO_FILE):
        return ["ERRORE TECNICO: File catalogo.txt non trovato."]
        
    try:
        with open(CATALOGO_FILE, "r", encoding="utf-8") as f:
            testo_catalogo = f.read()
        
        catalogo_pulito = testo_catalogo.replace("\r\n", "\n")
        blocchi = [b.strip() for b in catalogo_pulito.split("\n\n") if b.strip()]
        
        if len(blocchi) <= 1:
            righe = [r.strip() for r in catalogo_pulito.split("\n") if r.strip()]
            blocks = []
            for i in range(0, len(righe), 4):
                gruppo = "\n".join(righe[i:i+6])
                blocks.append(gruppo)
            blocchi = blocks
                
        matched_blocks = []
        for blocco in blocchi:
            blocco_n = normalize(blocco)
            
            if "cucin" in q or "ricett" in q or "mangiar" in q:
                if "cucin" in blocco_n or "ricett" in blocco_n or "gastronom" in blocco_n or "artusi" in blocco_n:
                    matched_blocks.append((blocco, 2))
                    continue

            score = sum(1 for term in terms if term in blocco_n)
            if score > 0:
                matched_blocks.append((blocco, score))
                    
        matched_blocks.sort(key=lambda x: -x[1])
        return [b[0] for b in matched_blocks[:max_results]]
    except Exception as e:
        return [f"ERRORE LETTURA: {str(e)}"]

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

def ask_gemini(user_message, text_results):
    if not text_results:
        return "Mi dispiace, nessun volume nel nostro catalogo corrisponde a questa richiesta."
        
    if "ERRORE" in text_results[0]:
        return text_results[0]

    context = "\n\n---\n\n".join(text_results)
    
    prompt_completo = (
        "Sei l'assistente virtuale della Biblioteca Belvedere di Siracusa (codice identificativo SBS0CB).\n"
        "Rispondi in modo cordiale, formale e conciso.\n\n"
        "NOTA: Il file del catalogo fornito contiene ESCLUSIVAMENTE i libri della Biblioteca Belvedere.\n\n"
        f"Dati del catalogo estratti:\n{context}\n\n"
        f"Richiesta dell'utente: {user_message}\n\n"
        "ISTRUZIONI:\n"
        "1. Elenca i libri pertinenti trovati indicandoli chiaramente.\n"
        "2. Per ogni libro indica: Titolo, Autore e la Collocazione.\n"
        "3. Usa un elenco puntato pulito ed elegante."
    )

    # TENTATIVO 1: Modello principale veloce
    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    # Se fallisce per Rate Limit (limite di richieste ravvicinate), aspettiamo e riproviamo
    if not response or response.status_code != 200:
        time.sleep(3) # Pausa di 3 secondi per far respirare la chiave API gratuita
        response = call_gemini_api("gemini-2.5-flash", prompt_completo)
        
    # TENTATIVO 2: Se è ancora bloccato, cambiamo modello passando al super stabile 1.5-flash
    if not response or response.status_code != 200:
        time.sleep(2)
        response = call_gemini_api("gemini-1.5-flash", prompt_completo)
        
    # TENTATIVO 3: Ultima spiaggia con il modello Pro
    if not response or response.status_code != 200:
        response = call_gemini_api("gemini-1.5-pro", prompt_completo)

    if not response or response.status_code != 200:
        return "I server della biblioteca sono momentaneamente carichi. Per favore, prova a ripetere la richiesta tra un istante."
            
    try:
        data = response.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return f"Errore decodifica testo IA Google: {str(e)}"

def send_telegram(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

def async_process_request(chat_id, text):
    results = search_text_catalog(text)
    reply = ask_gemini(text, results)
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
            send_telegram(chat_id, "Ciao! Sono l'assistente della Biblioteca Belvedere 📚 Scrivimi il titolo di un libro o un autore per cercarlo nel catalogo.")
            return "OK", 200
            
        thread = Thread(target=async_process_request, args=(chat_id, text))
        thread.start()
        
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
