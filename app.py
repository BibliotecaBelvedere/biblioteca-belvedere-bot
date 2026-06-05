import os
import json
import unicodedata
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Variabili d'ambiente (configurate su Render)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "biblioteca_belvedere_2024")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")

# Carica il catalogo
with open("catalogo.json", "r", encoding="utf-8") as f:
    CATALOG = json.load(f)

print(f"Catalogo caricato: {len(CATALOG)} titoli")

# Stop words italiane
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

# Sinonimi
SYNONYMS = {
    "bullismo": ["bullo","bullismo","violenza","sopruso","prepotenza"],
    "bullo": ["bullo","bullismo","prepotente"],
    "amore": ["amore","sentimento","romantico"],
    "guerra": ["guerra","conflitto","battaglia"],
    "sicilia": ["sicilia","siciliano","siculo","palermo","catania","siracusa"],
    "ragazzi": ["ragazzi","adolescenti","giovani","teenager"],
    "bambini": ["bambini","infanzia","fiabe","favole"],
    "giallo": ["giallo","poliziesco","detective","noir","thriller","mistero"],
    "fantasy": ["fantasy","magico","magia","drago","avventura"],
    "storia": ["storia","storico","storica","medioevo"],
    "cucina": ["cucina","ricette","gastronomia"],
    "fumetti": ["fumetti","manga","vignette"],
    "natura": ["natura","animali","ambiente","ecologia"],
    "psicologia": ["psicologia","mente","comportamento","psicanalisi"],
    "poesia": ["poesia","poesie","versi","liriche"],
    "scuola": ["scuola","educazione","insegnamento"],
}

def normalize(s):
    s = str(s).lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

def search_catalog(query, max_results=6):
    q = normalize(query)
    terms = [w for w in q.split() if len(w) > 2 and w not in STOPWORDS]
    if not terms:
        return []
    all_terms = list(terms)
    for term in terms:
        for key, syns in SYNONYMS.items():
            if term == key or term in syns:
                for s in syns:
                    if s not in all_terms:
                        all_terms.append(s)
    results = []
    for b in CATALOG:
        title_n = normalize(b["t"])
        author_n = normalize(b.get("a", ""))
        subjects_n = normalize(" ".join(b.get("s", [])))
        subtitle_n = normalize(b.get("st", ""))
        score = 0
        for term in all_terms:
            w = 1.0 if term in terms else 0.5
            if title_n.find(term) >= 0: score += 5 * w
            if author_n.find(term) >= 0: score += 4 * w
            if subjects_n.find(term) >= 0: score += 4 * w
            if subtitle_n.find(term) >= 0: score += 2 * w
        if score > 0:
            results.append({**b, "score": score})
    results.sort(key=lambda x: -x["score"])
    if results:
        max_score = results[0]["score"]
        threshold = max(3, max_score * 0.5)
        results = [r for r in results if r["score"] >= threshold]
    return results[:max_results]

def ask_claude(user_message, catalog_results):
    if catalog_results:
        context = "\n".join([
            f"- \"{b['t']}\""
            + (f": {b['st']}" if b.get("st") else "")
            + (f" di {b['a']}" if b.get("a") else "")
            + (f" ({b['y']})" if b.get("y") else "")
            + (f" [soggetti: {', '.join(b['s'])}]" if b.get("s") else "")
            + (f" - collocazione: {b['c']}" if b.get("c") else "")
            for b in catalog_results
        ])
    else:
        context = "Nessun risultato trovato nel catalogo."

    system_prompt = (
        "Sei l'assistente della Biblioteca Belvedere di Siracusa. "
        "Rispondi in italiano, in modo cordiale e conciso, adatto a un messaggio su Facebook/Instagram.\n\n"
        f"Risultati della ricerca nel catalogo:\n{context}\n\n"
        "ISTRUZIONI:\n"
        "1. Se ci sono risultati: scrivi una frase introduttiva breve, poi elenca i libri con titolo e collocazione.\n"
        "2. Se non ci sono risultati: dillo in una frase e suggerisci un termine alternativo.\n"
        "3. NON aggiungere domande finali o frasi di chiusura.\n"
        "4. NON inventare titoli non presenti nella lista.\n"
        "5. Massimo 300 caratteri in totale (siamo su Messenger).\n"
        "6. La risposta finisce dopo l'ultimo libro. Stop."
    )

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 300,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        },
        timeout=15,
    )
    data = response.json()
    return "".join(c.get("text", "") for c in data.get("content", []))

def send_message(recipient_id, text):
    """Invia un messaggio tramite Facebook Messenger API."""
    # Suddividi in messaggi da max 2000 caratteri se necessario
    chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
    for chunk in chunks:
        requests.post(
            "https://graph.facebook.com/v18.0/me/messages",
            params={"access_token": PAGE_ACCESS_TOKEN},
            json={
                "recipient": {"id": recipient_id},
                "message": {"text": chunk},
            },
            timeout=10,
        )

# ── Webhook ──────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Meta chiama questo endpoint per verificare il webhook."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Riceve i messaggi da Facebook/Instagram."""
    data = request.get_json()
    if not data or data.get("object") not in ("page", "instagram"):
        return "OK", 200

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = event.get("sender", {}).get("id")
            message = event.get("message", {})
            text = message.get("text", "").strip()
            if not text or not sender_id:
                continue
            # Cerca nel catalogo e rispondi
            results = search_catalog(text)
            reply = ask_claude(text, results)
            send_message(sender_id, reply)

    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return f"Assistente Biblioteca Belvedere attivo — {len(CATALOG)} titoli in catalogo.", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
