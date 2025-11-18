import os
import json
import math
import requests
from flask import Flask, request, render_template, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_API_KEY", "supersecretkey") # Fallback key per dev

# --- CONFIGURAZIONE DATABASE ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///flashcards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODELLI DATABASE ---
class Deck(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    topic = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    cards = db.relationship('Flashcard', backref='deck', lazy=True, cascade="all, delete-orphan")

class Flashcard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    front = db.Column(db.Text, nullable=False)
    back = db.Column(db.Text, nullable=False)
    deck_id = db.Column(db.Integer, db.ForeignKey('deck.id'), nullable=False)

# Inizializza il DB
with app.app_context():
    db.create_all()

# --- CONFIGURAZIONE GEMINI ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "INSERISCI_LA_TUA_GEMINI_API_KEY_QUI")
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

# --- FUNZIONI DI SUPPORTO ---

def clean_gemini_json(text):
    """Pulisce la risposta di Gemini per estrarre il JSON puro."""
    text = text.strip()
    # Rimuove markdown ```json ... ``` se presente
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("\n", 1)[0]
    
    # Cerca graffe se c'è testo sporco intorno
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        text = text[start:end+1]
    return text

def generate_questions_with_gemini(program_text: str, num_questions: int):
    # ... (TUA FUNZIONE ESISTENTE PER IL QUIZ - LA LASCIO UGUALE MA ABBREVIATA QUI PER SPAZIO)
    # ... COPIA INCOLLA LA TUA FUNZIONE QUI O MANTIENILA ...
    
    # NOTA: Per brevità nel codice finale, assumo che tu mantenga la tua funzione originale qui.
    # Riporto solo la parte di chiamata API per le flashcard sotto.
    pass 

# Riscrivo la funzione Quiz completa per assicurarci che funzioni tutto insieme
def generate_quiz_logic(program_text: str, num_questions: int):
    if num_questions <= 0: raise ValueError("Num > 0")
    desired_mcq = math.ceil(num_questions * 0.5)
    desired_open = num_questions - desired_mcq
    
    full_prompt = f"""
    Sei un generatore di quiz. Crea domande dal testo fornito.
    OBIETTIVO: {num_questions} domande ({desired_mcq} mcq, {desired_open} open).
    No formule. Solo teoria.
    FORMATO JSON: {{"questions": [{{ "text": "...", "qtype": "mcq", "options": ["..."], "answer": "..." }}]}}
    TESTO: {program_text}
    """
    
    payload = {"contents": [{"parts": [{"text": full_prompt}]}]}
    resp = requests.post(GEMINI_URL, headers={"Content-Type": "application/json"}, json=payload)
    resp.raise_for_status()
    
    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        text = clean_gemini_json(text) # Usa la funzione helper
        # Fallback parsing specifico per oggetto singolo
        if text.strip().startswith("{"):
            parsed = json.loads(text)
        else:
             # Se inizia con [ prova a wrapparlo o cercare l'oggetto
             start = text.find("{")
             end = text.rfind("}")
             parsed = json.loads(text[start:end+1])

        return parsed["questions"]
    except Exception as e:
        raise RuntimeError(f"Errore parsing Gemini: {e}")

def generate_flashcards_logic(program_text: str, num_cards: int):
    """Genera flashcard (Fronte/Retro) usando Gemini."""
    prompt = f"""
    Crea {num_cards} flashcard basate su questo testo: "{program_text}".
    Le flashcard servono per la ripetizione spaziata.
    
    STRUTTURA:
    - "front": La domanda, il concetto o il termine chiave.
    - "back": La risposta, la definizione o la spiegazione (concisa).
    
    FORMATO OUTPUT:
    Restituisci SOLO un array JSON valido. Esempio:
    [
        {{"front": "Cos'è la mitosi?", "back": "Processo di divisione cellulare..."}},
        {{"front": "Formula velocità", "back": "Spazio fratto tempo"}}
    ]
    Non aggiungere altro testo.
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = requests.post(GEMINI_URL, headers={"Content-Type": "application/json"}, json=payload)
    resp.raise_for_status()
    
    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        text = clean_gemini_json(text)
        cards = json.loads(text)
        return cards
    except Exception as e:
        raise RuntimeError(f"Errore generazione flashcard: {e}")

# --- ROTTE ---

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate_quiz():
    # Questa rotta gestisce sia Quiz che Flashcard in base al bottone premuto
    program_text = request.form.get("program_text", "").strip()
    num_input = request.form.get("num_questions", "").strip()
    action = request.form.get("action", "quiz") # Default quiz

    if not program_text:
        flash("Devi incollare il contenuto del programma.")
        return redirect(url_for("index"))
    
    if not num_input.isdigit():
        flash("Numero non valido.")
        return redirect(url_for("index"))
    
    count = int(num_input)
    
    if action == "flashcards":
        # --- LOGICA FLASHCARD ---
        try:
            cards_data = generate_flashcards_logic(program_text, count)
            
            # Salva nel DB
            title = program_text[:40] + "..." if len(program_text) > 40 else program_text
            new_deck = Deck(topic=title)
            db.session.add(new_deck)
            db.session.commit()
            
            for c in cards_data:
                card = Flashcard(front=c['front'], back=c['back'], deck=new_deck)
                db.session.add(card)
            db.session.commit()
            
            return redirect(url_for('view_flashcards', deck_id=new_deck.id))
            
        except Exception as e:
            flash(f"Errore Flashcard: {e}")
            return redirect(url_for("index"))
            
    else:
        # --- LOGICA QUIZ (Tua esistente) ---
        try:
            # Nota: qui chiamo la funzione logica ricostruita sopra
            questions = generate_quiz_logic(program_text, count)
            session["questions"] = questions
            return render_template("quiz.html", questions=questions)
        except Exception as e:
            flash(f"Errore Quiz: {e}")
            return redirect(url_for("index"))

@app.route("/submit", methods=["POST"])
def submit_quiz():
    # ... (La tua logica di submit_quiz esistente rimane identica) ...
    questions = session.get("questions")
    if not questions:
        return redirect(url_for("index"))
        
    score = 0.0
    correct = 0; wrong = 0; blank = 0; details = []
    
    for i, q in enumerate(questions):
        ans = request.form.get(f"q{i}", "").strip()
        is_correct = False
        
        if not ans:
            result = "blank"; blank += 1
        else:
            if q["qtype"] == "mcq":
                is_correct = (ans == q["answer"])
            else:
                is_correct = (ans.lower() == q["answer"].lower())
            
            if is_correct:
                score += 1.0; correct += 1; result = "correct"
            else:
                score -= 0.1; wrong += 1; result = "wrong"
        
        details.append({"text": q["text"], "user_answer": ans, "correct_answer": q["answer"], "result": result})

    return render_template("result.html", total=len(questions), correct=correct, wrong=wrong, blank=blank, score=f"{score:.2f}", details=details)

# --- NUOVE ROTTE FLASHCARD ---

@app.route('/flashcards/<int:deck_id>')
def view_flashcards(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    return render_template('flashcard_player.html', deck=deck)

@app.route('/saved_flashcards')
def saved_flashcards():
    decks = Deck.query.order_by(Deck.created_at.desc()).all()
    return render_template('saved_flashcards.html', decks=decks)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
