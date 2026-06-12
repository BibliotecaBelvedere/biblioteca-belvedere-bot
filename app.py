import os
import time
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
        
        # VERIFICATO: Qui usiamo 'contenuto' con la U. Nessun refuso.
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

def call_gemini_api(model_name, prompt_text):
    try:
        url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt_text}]}]},
            timeout=12
        )
        return response
    except:
        return None

def ask_gemini(user_message, text_results):
    if not text_results:
        return "Mi dispiace, nessun volume nel nostro catalogo sembra corrispondere a questa ricerca tematica."
        
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

    # TENTATIVO 1: Canale principale veloce
    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    # TENTATIVO 2: Se va in timeout o dà errore, aspetta 2 secondi e riprova sul principale
    if not response or response.status_code != 200:
        time.sleep(2)
        response = call_gemini_api("gemini-2.5-flash", prompt_completo)
        
    # TENTATIVO 3: Modello di riserva stabile v1 accettato universalmente
    if not response or response.status_code != 200:
        response = call_gemini_api("gemini-1.5-pro", prompt_completo)

    if not response or response.status_code != 200:
        return "I server della biblioteca sono momentaneamente carichi. Per favore, prova a ripetere la richiesta tra un istante."
            
    try:
        data = response.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return f"Errore decod
