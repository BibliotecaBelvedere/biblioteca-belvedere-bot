import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# CATALOGO FINTO DI TEST (Scritto direttamente qui, zero file esterni da caricare!)
LIBRI_TEST = [
    "Titolo: Il nome della rosa - Autore: Umberto Eco - Collocazione: Sala A, Scaffale 3",
    "Titolo: Il pendolo di Foucault - Autore: Umberto Eco - Collocazione: Sala A, Scaffale 4",
    "Titolo: Trent'anni e un giorno - Autore: Testo Esempio - Collocazione: I 19-1"
]

def search_test(query):
    q = query.lower()
    risultati = []
    for libro in LIBRI_TEST:
        if q in libro.lower():
            risultati.append(libro)
    return risultati

def ask_claude(user_message, text_results):
    if not text_results:
        return "Mi dispiace, questo volume non risulta nel catalogo di test della nostra sede."

    context = "\n".join(text_results)
    system_prompt = (
        "Sei l'assistente della Biblioteca Belvedere. Rispondi in modo conciso.\n\n"
        f"Dati del catalogo:\n{context}\n\n"
        "Mostra il titolo e la collocazione esatta. Non aggiungere altro."
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
                "max_tokens": 200,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=10,
        )
        return "".join(c.get("text", "") for c in response.json().get("content", []))
    except:
        return "Errore di comunicazione con l'IA."

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
        send_telegram(chat_id, "TEST ATTIVO! Il server risponde. Scrivimi un titolo (es. 'rosa' o 'eco') per testare la ricerca.")
        return "OK", 200
        
    results = search_test(text)
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
    return "Server in modalità TEST di isolamento.", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
