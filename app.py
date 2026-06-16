def ask_gemini(user_message, testi_libri):
    if not testi_libri:
        return "Mi dispiace, nessun volume nel nostro catalogo corrisponde a questa richiesta al momento."
    
    # LIMITIAMO I LIBRI E DIAMO SOLO LE RIGHE ESSENZIALI PER ABBATTERE I TEMPI DI RISPOSTA
    context_list = []
    for blocco in testi_libri[:20]:  # Massimo 20 candidati per non sovraccaricare la memoria
        linee = [l.strip() for l in blocco.split('\n') if l.strip()]
        # Prendiamo solo le prime 2 linee (Titolo e Autore) che bastano all'AI per filtrare
        blocco_super_compatto = " | ".join(linee[:2])
        context_list.append(blocco_super_compatto)
        
    context = "\n---\n".join(context_list)
    
    prompt_completo = (
        "Sei l'assistente ufficiale della Biblioteca Belvedere di Siracusa (SBS0CB).\n"
        f"L'utente cerca: '{user_message}'\n\n"
        f"Lista libri:\n{context}\n\n"
        "COMPITO:\n"
        "1. Escludi i libri totalmente fuori tema.\n"
        "2. Elenca i volumi coerenti (formato: **Titolo**, Autore - Collocazione).\n"
        "3. Saluta invitando l'utente in biblioteca a Siracusa."
    )

    # Chiamiamo l'AI con il prompt ultraleggero
    response = call_gemini_api("gemini-2.5-flash", prompt_completo)
    
    # EMERGENZA POTENZIATA INOSSIDABILE (Se la rete salta comunque, Python risponde pulito)
    if not response or response.status_code != 200:
        q_clean = normalize(user_message)
        parole_ricerca = [w for w in q_clean.split() if len(w) >= 3 and w not in STOPWORDS]
        
        linee_emergenza = [
            "📚 **Biblioteca Belvedere (SBS0CB) - Risultati Ricerca**:\n"
        ]
        
        PAROLE_BANDITE_MANGA = ["esopo", "aesopus", "favole", "biagi", "mastroianni", "brecht", "barbaro"]
        contatore = 0
        
        for blocco in testi_libri:
            testo_norm = normalize(blocco)
            
            if "manga" in q_clean or "fumett" in q_clean:
                if any(bad in testo_norm for bad in PAROLE_BANDITE_MANGA):
                    continue
            
            if any(p in testo_norm for p in parole_ricerca) or "fumett" in testo_norm or "giallo" in testo_norm or "cucin" in testo_norm:
                linee = [l.strip() for l in blocco.split('\n') if l.strip()]
                info_libro = " - ".join(linee[:2]) # Mostriamo solo titolo e autore anche in emergenza
                linee_emergenza.append(f"• {info_libro}")
                contatore += 1
            if contatore >= 12:
                break
                
        if contatore == 0:
            return "Siamo spiacenti, nessun volume corrisponde alla ricerca corrente."
            
        linee_emergenza.append("\n_Nota: Per consultare i volumi, ti aspettiamo in sede a Siracusa._")
        return "\n".join(linee_emergenza)
            
    try:
        data = response.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except:
        return "Errore di lettura dei dati dall'intelligenza artificiale."
