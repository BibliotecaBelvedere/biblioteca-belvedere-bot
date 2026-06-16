import os
import re
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

DB_FILE = "catalogo.db"

STOPWORDS = {
    'che','del','della','delle','degli','dei','dal','dalla','dalle','dagli','dai',
    'nel','nella','nelle','negli','nei','sul','sulla','sulle','sugli','sui','per',
    'con','una','uno','gli','alla','allo','alle','agli','col','coi','tra','fra',
    'non','qui','qua','sua','suo','suoi','sue','mio','mia','miei','mie','tuo',
    'tua','tuoi','tue','questo','questa','questi','queste','quello','quella',
    'quelli','quelle','anche','como','dove','quando','mentre','essere','avere',
    'fare','dire','cerca','cerco','vorrei','voglio','cercare','trovare','libro',
    'libri','testo','testi','parli','parla','parlano','riguarda','riguardano',
    'tratta','trattano','scritto','scritta','uscito','uscita','hai','avete','trova','un',
    'mi','dai','dacci','dimmi','trovami','cercami','sono','ci','adatti','alle',
    'crechi','creca','su','di','da','a','in','qualcosa',
    'mostrami','elenco','lista','autori','autore','volumi','volume','titoli','titolo'
}

# Mappatura tematica solida per evitare falsi positivi (es. Annarosa o Asor Rosa)
TEMI_ESPANSI = {
    "cucin": ["cucin", "ricett", "gastronom", "diet", "piatt", "aliment", "mangiare", "artusi"],
    "teatr": ["teatr", "commedia", "tragedia", "dramma", "pirandello", "goldoni", "shakespeare"],
    "amor": ["modignani", "steel", "sparks", "allende", "romance", "passion"],
    "rosa": ["modignani", "steel", "sparks", "allende", "romance", "passion"],
    "bullis": ["bullis", "bullo", "cyberbulli", "violenza", "scuola", "ragazzi"],
    "giallo": ["giallo", "gialli", "thriller", "poliziesc", "assassin", "delitto", "mistero", "christie", "camilleri"],
    "noir": ["noir", "poliziesco", "crimine", "indagine", "carlotto", "carofiglio", "indridason"],
    "avventur": ["avventur", "azione", "esplorazione", "viaggio", "salgari", "verne"],
    "fantasy": ["fantasy", "fantastico", "magia", "drago", "tolkien", "rowling", "martin"],
    "manga": ["manga", "fumetto", "fumetti", "giappone", "shinzo"],
    "fumett": ["fumett", "manga", "albo", "strisce", "zerocalcare", "tex"]
}

def normalize(s):
    s = str(s).lower().strip()
    for c in ['?', '!', ',', '.', ';', ':', '-', '_', '*', '"', "'"]:
        s = s.replace(c, ' ')
    s = s.replace('č', 'c').replace('š', 's').replace('ś', 's').replace('ā', 'a')
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
        return f"ERRORE: Il file catalogo.txt NON esiste."

    try:
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
        
        with open(file_reale, "r", encoding="utf-8-sig", errors="ignore") as f:
            contenuto = f.read().replace("\r\n", "\n").replace("\u00a0", "\n")
            
        pezzi_raw = contenuto.split("[nd]")
        blocchi_effettivi = []
        
        for pezzo in pezzi_raw:
            linee = [l.strip() for l in pezzo.split("\n") if l.strip()]
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
        conn.close()
        return f"SUCCESS: Caricati {len(blocchi_effettivi)} libri."
    except Exception as e:
        return f"ERRORE: {str(e)}"

def cerca_nel_db(query):
    q = normalize(query)
    # Estraiamo le parole pulite ignorando le stopword
    parole_chiave = [w for w in q.split() if len(w) >= 2 and w not in STOPWORDS]
    
    if not parole_chiave:
        return []

    termini_ricerca = set(parole_chiave)
    genere_rosa_attivo = any(k in q for k in ["rosa", "amor", "sentiment"])

    # Espansione dei termini in base ai macro-temi
    for pk in parole_chiave:
        for radice, sinonimi in TEMI_ESPANSI.items():
            if radice in pk or pk in radice:
                termini_ricerca.update(sinonimi)

    termini_ricerca = list(termini_ricerca)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Costruiamo una query SQL solida basata su OR per i temi espansi
    condizioni = ["testo_normalizzato LIKE ?" for _ in termini_ricerca]
    parametri = [f"%{t}%" for t in termini_ricerca]
    
    sql_query = f"SELECT testo_completo, testo_normalizzato FROM libri WHERE {' OR '.join(condizioni)} LIMIT 50"
    cursor.execute(sql_query, parametri)
    righe = cursor.fetchall()
    conn.close()
    
    libri_filtrati = []
    for testo_completo, testo_norm in righe:
        # Pulizia di sicurezza: scartiamo i blocchi "orfani" cortissimi (es. solo il nome dell'autore senza titoli)
        if len(testo_completo.strip()) < 35:
            continue
            
        punteggio = 0
        
        # Filtro stringente anti-falsi positivi per il genere Rosa
        if genere_rosa_attivo:
            # Se trova le autrici reali diamo un bonus stratosferico
            if any(a in testo_norm for a in ["modignani", "steel", "sparks", "allende"]):
                punteggio += 200
            # Se trova falsi positivi come Asor Rosa o saggistica medievale/psicologica, penalizziamo duramente
            if "asor" in testo_norm or "alberoni" in testo_norm or "medioevo" in testo_norm:
                punteggio -= 150
            # Cerca la parola "rosa" isolata e non dentro "annarosa" o "mariarosa"
            if re.search(r'\brosa\b', testo_norm):
                punteggio += 30

        # Punteggio standard basato sulle parole cercate dall'utente
        for pk in parole_chiave:
            if pk in testo_norm:
                punteggio += 20
                
        libri_filtrati.append((punteggio, testo_completo))
        
    # Ordiniamo per punteggio decrescente e restituiamo i migliori
    libri_filtrati.sort(key=lambda x: x[0], reverse=True)
    return [l[1] for l in libri_filtrati if l[0] >= 0]

def call_gemini_api(model_name, prompt_text):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
            "generationConfig": {"temperature": 0.1}
        }
        response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=12)
        return response
    except:
        return None

def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Mi dispiace, nessun volume nel nostro catalogo corrisponde esattamente a questa richiesta. Ti invitiamo a consultare il bibliotecario in sede a Siracusa per una ricerca più approfondita."
    
    context_list = []
    for i, blocco in enumerate(testi_libri[:15]):
        context_list.append(f"CANDIDATO #{i+1}:\n{blocco}")
        
    context = "\n\n---\n\n".join(context_list)
    
    prompt_completo = (
        "Sei l'assistente virtuale ufficiale della Biblioteca Belvedere di Siracusa (SBS0CB).\n"
        f"L'utente cerca: '{user_message}'\n\n"
        f"Lista libri candidati estratti dal database:\n\n{context}\n\n"
        "REGOLE DI SELEZIONE RIGIDE:\n"
        "1. Se l'utente cerca 'romanzi rosa' o d'amore, includi SOLO romanzi narrativi sentimentali reali (es. Sveva Casati Modignani, Pearl Abraham se pertinente, ecc.).\n"
        "2. Escludi tassativamente saggistica letteraria (es. Asor Rosa), saggi storici, libri per bambini o fumetti non pertinenti.\n"
        "3. Se l'utente cerca 'bullismo', seleziona tutti i testi pertinenti estratti che parlano di bullismo, violenza tra ragazzi o disagio adolescenziale.\n"
        "4. Genera un elenco puntato chiaro in questo formato:\n"
        "   * **Titolo del Libro**, Autore - Collocazione\n"
        "5. Concludi SEMPRE con una nota fissa che indichi che la risposta è parziale e invita a consultare il bibliotecario in sede a Siracusa per ulteriori informazioni."
    )

    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    # EMERGENZA RIGIDA IN CASO DI KO GENERALE
    if not response or response.status_code != 200:
        linee_emergenza = ["📚 **Biblioteca Belvedere (SBS0CB) - Risultati Ricerca (Parziale)**:\n"]
        for blocco in testi_libri[:6]:
            linee = [l.strip() for l in blocco.split('\n') if l.strip()]
            info_libro = " - ".join(linee[:3])
            linee_emergenza.append(f"• {info_libro}")
            
        linee_emergenza.append("\n_Nota: Questa risposta è parziale. Ti invitiamo a consultare il bibliotecario in sede a Siracusa per ulteriori notizie e informazioni complete._")
        return "\n".join(linee_emergenza)
            
    try:
        data = response.json()
        output_ai = data['candidates'][0]['content']['parts'][0]['text']
        if "bibliotecario" not in output_ai.lower():
            output_ai += "\n\n_Nota: Questa risposta è parziale. Si invita a consultare il bibliotecario in sede a Siracusa per ulteriori notizie e informazioni complete._"
        return output_ai
    except:
        return "Errore di elaborazione dei dati. Si invita a consultare il bibliotecario in sede a Siracusa."

def send_telegram(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except:
        pass

def async_process_request(chat_id, text):
    try:
        libri_trovati = cerca_nel_db(text)
        reply = ask_gemini(text, libri_trovati)
        send_telegram(chat_id, reply)
    except:
        send_telegram(chat_id, "Si è verificato un errore di sistema. Consultare il bibliotecario in sede.")

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
            send_telegram(chat_id, "Benvenuto nell'assistente della Biblioteca Belvedere! 📚 Scrivimi un autore, un titolo o un argomento per avviare la ricerca.")
            return "OK", 200
            
        thread = Thread(target=async_process_request, args=(chat_id, text))
        thread.start()
    except:
        pass
    return "OK", 200

@app.route("/setup", methods=["GET"])
def setup():
    res = inizializza_database()
    try:
        render_url = request.host_url.rstrip("/")
        resp = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": f"{render_url}/webhook_biblioteca"}, timeout=10)
        tele_res = resp.json()
    except Exception as e:
        tele_res = f"Errore connessione: {str(e)}"
    return jsonify({"status": res, "telegram_response": tele_res})

@app.route("/", methods=["GET"])
def home():
    return "Assistente Biblioteca Pronto.", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
