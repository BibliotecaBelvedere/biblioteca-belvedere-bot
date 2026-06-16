import os
import sqlite3
import requests
import unicodedata
from flask import Flask, request, jsonify
from threading import Thread

app = Flask(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

DB_FILE = "catalogo.db"

STOPWORDS = {'che', 'del', 'della', 'di', 'da', 'in', 'per', 'con', 'su', 'a', 'un', 'una', 'il', 'la', 'i', 'gli', 'le', 'mi', 'ti', 'ci', 'cerca', 'cerco', 'trova'}

def normalize(s):
    s = str(s).lower().strip()
    for c in ['?', '!', ',', '.', ';', ':', '-', '_', '*', '"', "'"]:
        s = s.replace(c, ' ')
    s = unicodedata.normalize("NFD", s)
    return " ".join("".join(c for c in s if unicodedata.category(c) != "Mn").split())

def inizializza_database():
    file_presenti = os.listdir(".")
    file_reale = None
    for f_name in file_presenti:
        if f_name.lower() == "catalogo.txt":
            file_reale = f_name
            break
    if not file_reale:
        return "ERRORE: Il file catalogo.txt NON esiste."
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS libri (id INTEGER PRIMARY KEY AUTOINCREMENT, testo_completo TEXT, testo_normalizzato TEXT)')
        cursor.execute("DELETE FROM libri")
        
        with open(file_reale, "r", encoding="utf-8-sig", errors="ignore") as f:
            contenuto = f.read().replace("\r\n", "\n").replace("\u00a0", "\n")
            
        pezzi_raw = contenuto.split("[nd]")
        blocchi_effettivi = []
        for pezzo in pezzi_raw:
            linee = [l.strip() for l in pezzo.split("\n") if l.strip()]
            linee_pulite = [l for l in linee if "Ordinamento" not in l and "Biblioteca:" not in l and "Data e ora:" not in l]
            if linee_pulite:
                blocchi_effettivi.append("\n".join(linee_pulite))

        for blocco in blocchi_effettivi:
            cursor.execute("INSERT INTO libri (testo_completo, testo_normalizzato) VALUES (?, ?)", (blocco, normalize(blocco)))
        conn.commit()
        conn.close()
        return f"SUCCESS: Caricati {len(blocchi_effettivi)} libri."
    except Exception as e:
        return f"ERRORE: {str(e)}"

def cerca_nel_db(query):
    q = normalize(query)
    parole = [w for w in q.split() if w not in STOPWORDS and len(w) >= 2]
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Se la ricerca tocca macro-temi, carichiamo un set generoso ma ottimizzato (fino a 120 libri)
    if any(g in q for g in ["giallo", "gialli", "rosa", "amor", "bullis", "bullo", "cucin", "noir", "storia", "scuola"]):
        cursor.execute("""
            SELECT testo_completo FROM libri 
            WHERE testo_normalizzato LIKE '%giallo%' OR testo_normalizzato LIKE '%gialli%' 
               OR testo_normalizzato LIKE '%christie%' OR testo_normalizzato LIKE '%simenon%' OR testo_normalizzato LIKE '%camilleri%'
               OR testo_normalizzato LIKE '%rosa%' OR testo_normalizzato LIKE '%amor%' OR testo_normalizzato LIKE '%modignani%' OR testo_normalizzato LIKE '%steel%'
               OR testo_normalizzato LIKE '%bullis%' OR testo_normalizzato LIKE '%bullo%' OR testo_normalizzato LIKE '%scuola%' OR testo_normalizzato LIKE '%violenz%'
               OR testo_normalizzato LIKE '%cucin%' OR testo_normalizzato LIKE '%ricett%' OR testo_normalizzato LIKE '%artusi%'
               OR testo_normalizzato LIKE '%noir%' OR testo_normalizzato LIKE '%carlotto%'
            LIMIT 120
        """)
    else:
        condizioni = ["testo_normalizzato LIKE ?" for _ in parole]
        parametri = [f"%{p}%" for p in parole]
        if condizioni:
            cursor.execute(f"SELECT testo_completo FROM libri WHERE {' OR '.join(condizioni)} LIMIT 80", parametri)
        else:
            cursor.execute("SELECT testo_completo FROM libri LIMIT 50")
            
    righe = cursor.fetchall()
    conn.close()
    return [r[0] for r in righe]

def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Gentile utente, non ho trovato corrispondenze dirette nel catalogo elettronico. Ti invitiamo a rivolgerti al bibliotecario in sede a Siracusa per una ricerca approfondita tra i volumi fisici."

    # OTTIMIZZAZIONE ESSENZIALE: Estraiamo solo le informazioni identificative di ogni libro
    # eliminando la spazzatura tipografica (misure in cm, codici a barre lunghi, info di editing)
    elenco_snello = []
    for blocco in testi_libri:
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        if linee:
            # Prendiamo solo le prime 2 o 3 righe significative del blocco (Titolo, Autore, Note essenziali)
            estratto = " / ".join(linee[:3])
            elenco_snello.append(estratto)
            
    context = "\n".join([f"- {item}" for item in elenco_snello])
    
    prompt_completo = (
        "Sei il Bibliotecario Virtuale della Biblioteca Belvedere di Siracusa, una guida colta e appassionata di letteratura.\n"
        "Il tuo scopo è fornire una CONSULENZA BIBLIOGRAFICA RAGIONATA E CRITICA basandoti sui libri realmente disponibili.\n\n"
        f"L'utente desidera: '{user_message}'\n\n"
        "ISTRUZIONI IMPORTANTI:\n"
        "1. Usa la tua cultura enciclopedica per raggruppare i libri dell'elenco sottostante per genere o autore pertinente (es. se l'utente chiede 'gialli', riconosci autonomamente Georges Simenon, Agatha Christie o Camilleri presenti nella lista).\n"
        "2. Non fare un elenco freddo. Scrivi una risposta discorsiva: introduci l'argomento e presenta una selezione dei 4-7 libri più calzanti della lista, aggiungendo per ognuno un breve commento sul perché vale la pena leggerlo.\n"
        "3. Per ogni libro citato inserisci chiaramente Titolo, Autore e la sua Collocazione (es. I 23b-2 o 29-4) che leggi nell'elenco.\n"
        "4. Se l'utente nomina un libro o un autore famoso che NON è presente nell'elenco, spiega brevemente cos'è usando le tue conoscenze globali, ma proponi subito come alternativa i libri affini che sono invece presenti nel catalogo.\n"
        "5. Concludi sempre ricordando che la risposta è parziale e invita l'utente a consultare il bibliotecario in sede a Siracusa per ulteriori notizie, consigli personalizzati e per esplorare l'intero catalogo.\n\n"
        f"Ecco l'elenco dei libri disponibili in biblioteca su cui costruire la tua recensione:\n{context}"
    )

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        response = requests.post(
            url, 
            headers={"Content-Type": "application/json"}, 
            json={"contents": [{"role": "user", "parts": [{"text": prompt_completo}]}], "generationConfig": {"temperature": 0.4}}, 
            timeout=15
        )
        
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
    except:
        pass

    # EMERGENZA ELEGANTE IN CASO DI TIMEOUT INTERNO DELL'API
    linee_emergenza = ["📚 **Biblioteca Belvedere (SBS0CB) - Servizio Bibliografico**:\n", "Gentile utente, la selezione per questa tematica è molto ampia. Ecco i primi titoli storici individuati nel nostro catalogo:\n"]
    for blocco in testi_libri[:5]:
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        linee_emergenza.append(f"• {' - '.join(linee[:2])}")
    linee_emergenza.append("\n_Nota: Questa selezione è parziale. Ti invitiamo in sede a Siracusa dove il Bibliotecario potrà comporre per te una bibliografia ragionata e completa._")
    return "\n".join(linee_emergenza)

def send_telegram(chat_id, text):
    try: requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except: pass

def async_process_request(chat_id, text):
    try:
        libri_trovati = cerca_nel_db(text)
        reply = ask_gemini(text, libri_trovati)
        send_telegram(chat_id, reply)
    except:
        send_telegram(chat_id, "Gentile utente, il sistema ha riscontrato un imprevisto. Il bibliotecario in sede a Siracusa rimane a tua completa disposizione.")

@app.route("/webhook_biblioteca", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json()
        if data and "message" in data:
            chat_id = data["message"]["chat"]["id"]
            text = data["message"].get("text", "").strip()
            if text == "/start":
                send_telegram(chat_id, "Benvenuto al servizio di consulenza bibliografica della Biblioteca Belvedere! Chiedimi consigli di lettura, percorsi tematici o bibliografie d'autore.")
            elif chat_id and text:
                Thread(target=async_process_request, args=(chat_id, text)).start()
    except: pass
    return "OK", 200

@app.route("/setup", methods=["GET"])
def setup():
    res = inizializza_database()
    try:
        render_url = request.host_url.rstrip("/")
        requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": f"{render_url}/webhook_biblioteca"}, timeout=10)
        tele_res = "Webhook configurato ed attivato."
    except Exception as e: tele_res = str(e)
    return jsonify({"status": res, "telegram_response": tele_res})

@app.route("/", methods=["GET"])
def home(): return "Consulenza Bibliografica Attiva.", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
