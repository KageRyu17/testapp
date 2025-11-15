import os
import json
import math
import textwrap
from datetime import datetime
from uuid import uuid4

import requests
from PyPDF2 import PdfReader
from flask import (
    Flask,
    request,
    render_template,
    redirect,
    url_for,
    session,
    flash,
)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_API_KEY", "super-secret-key")  # fallback di sicurezza

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")

os.makedirs(DATA_DIR, exist_ok=True)


def _load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        return {}


def _save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as fh:
        json.dump(users, fh, ensure_ascii=False, indent=2)


def get_current_username():
    return session.get("username")


def get_user_data(username):
    if not username:
        return None
    users = _load_users()
    return users.get(username)


def get_user_history(username):
    data = get_user_data(username)
    if not data:
        return []
    return data.get("history", [])


def store_quiz_generation(username, questions, program_text):
    users = _load_users()
    user = users.get(username)
    if not user:
        return None

    history = user.setdefault("history", [])
    entry_id = str(uuid4())
    preview = " ".join(program_text.split())[:160]
    history_entry = {
        "id": entry_id,
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "num_questions": len(questions),
        "preview": preview,
        "questions": questions,
        "last_result": None,
    }
    history.insert(0, history_entry)
    _save_users(users)
    return entry_id


def update_quiz_history(username, quiz_id, payload):
    users = _load_users()
    user = users.get(username)
    if not user:
        return
    history = user.get("history", [])
    for entry in history:
        if entry.get("id") == quiz_id:
            entry.update(payload)
            break
    _save_users(users)


def render_with_history(template_name, **context):
    username = get_current_username()
    context.setdefault("history", get_user_history(username))
    return render_template(template_name, **context)


@app.context_processor
def inject_user():
    return {"current_user": get_current_username()}

# Chiave e modello Gemini
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "INSERISCI_LA_TUA_GEMINI_API_KEY_QUI")
GEMINI_MODEL = "gemini-2.0-flash"

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)


def extract_text_from_pdf(uploaded_file) -> str:
    """Estrae il testo dal PDF caricato."""

    if not uploaded_file:
        raise ValueError("Nessun file PDF ricevuto.")

    uploaded_file.stream.seek(0)

    try:
        reader = PdfReader(uploaded_file.stream)
    except Exception as exc:  # pragma: no cover - PyPDF2 error paths
        raise ValueError("Impossibile leggere il PDF caricato.") from exc

    extracted_parts = []

            {
                "parts": [
                    {"text": full_prompt.strip()}
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json"
    }

    resp = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=240)
    resp.raise_for_status()
    data = resp.json()

    try:
        gemini_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Risposta di Gemini inattesa: {e}\n\n{data}")

    questions = _parse_questions_json(gemini_text, num_questions)
    return questions


@app.route("/", methods=["GET"])
def index():
    return render_with_history("index.html")


@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if len(username) < 3 or len(password) < 6:
        flash("Inserisci un username (min 3 caratteri) e una password di almeno 6 caratteri.")
        return redirect(url_for("index"))

    users = _load_users()
    if username in users:
        flash("Questo username è già in uso. Scegline un altro.")
        return redirect(url_for("index"))

    users[username] = {
        "password": generate_password_hash(password),
        "history": [],
    }
    _save_users(users)
    session["username"] = username
    flash("Registrazione completata! Ora puoi generare i tuoi quiz.")
    return redirect(url_for("index"))


@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    users = _load_users()
    user = users.get(username)
    if not user or not check_password_hash(user.get("password", ""), password):
        flash("Credenziali non valide. Riprova.")
        return redirect(url_for("index"))

    session["username"] = username
    flash(f"Bentornato {username}!")
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("username", None)
    session.pop("questions", None)
    session.pop("active_history_id", None)
    flash("Sei uscito dall'account.")
    return redirect(url_for("index"))


@app.route("/generate", methods=["POST"])
def generate_quiz():
    username = get_current_username()
    if not username:
        flash("Devi effettuare l'accesso per generare un quiz.")
        return redirect(url_for("index"))

    program_text = request.form.get("program_text", "").strip()
    num_questions_str = request.form.get("num_questions", "").strip()
    uploaded_pdf = request.files.get("program_pdf")

    # Se è stato caricato un PDF, sovrascrivo program_text con il contenuto del PDF
    if uploaded_pdf and uploaded_pdf.filename:
        if not uploaded_pdf.filename.lower().endswith(".pdf"):
            flash("Carica un file PDF valido.")
            return redirect(url_for("index"))

        try:
            program_text = extract_text_from_pdf(uploaded_pdf)
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("index"))

    if not program_text:
        flash("Devi incollare il programma o caricare un PDF.")
        return redirect(url_for("index"))

    if not num_questions_str.isdigit():
        flash("Il numero di domande deve essere un intero.")
        return redirect(url_for("index"))

    num_questions = int(num_questions_str)
    if num_questions <= 0 or num_questions > 50:
        flash("Il numero di domande deve essere tra 1 e 50.")
        return redirect(url_for("index"))

    try:
        questions = generate_questions_with_gemini(program_text, num_questions)
    except Exception as e:
        flash(f"Errore nella generazione delle domande: {e}")
        return redirect(url_for("index"))

    session["questions"] = questions
    quiz_id = store_quiz_generation(username, questions, program_text)
    session["active_history_id"] = quiz_id
    return render_with_history("quiz.html", questions=questions)


@app.route("/submit", methods=["POST"])
def submit_quiz():
    questions = session.get("questions")
    if not questions:
        flash("Nessun quiz attivo. Genera prima un quiz.")
        return redirect(url_for("index"))

    details = []
    correct = wrong = blank = 0

    for idx, q in enumerate(questions):
        field_name = f"q{idx}"
        given = (request.form.get(field_name) or "").strip()
        correct_answer = str(q.get("answer", "")).strip()

        if not given:
            result = "blank"
            blank += 1
        elif given.lower() == correct_answer.lower():
            result = "correct"
            correct += 1
        else:
            result = "wrong"
            wrong += 1

        details.append(
            {
                "text": q.get("text", ""),
                "qtype": q.get("qtype", ""),
                "options": q.get("options"),
                "user_answer": given or None,
                "correct_answer": correct_answer,
                "result": result,
            }
        )

    total = len(questions)
    score_value = round(correct - wrong * 0.25, 2)

    username = get_current_username()
    quiz_id = session.get("active_history_id")
    if username and quiz_id:
        update_quiz_history(
            username,
            quiz_id,
            {
                "last_result": {
                    "score": score_value,
                    "correct": correct,
                    "wrong": wrong,
                    "blank": blank,
                    "total": total,
                    "details": details,
                }
            },
        )

    session.pop("questions", None)
    session.pop("active_history_id", None)

    return render_with_history(
        "result.html",
        score=score_value,
        correct=correct,
        wrong=wrong,
        blank=blank,
        total=total,
        details=details,
    )


@app.route("/history/<quiz_id>", methods=["GET"])
def history_detail(quiz_id):
    username = get_current_username()
    if not username:
        flash("Effettua il login per consultare la tua cronologia.")
        return redirect(url_for("index"))

    history = get_user_history(username)
    entry = next((item for item in history if item.get("id") == quiz_id), None)
    if not entry:
        flash("Quiz non trovato nella tua cronologia.")
        return redirect(url_for("index"))

    return render_with_history("history_detail.html", entry=entry)


if __name__ == "__main__":
    # IMPORTANTISSIMO per Render: usa la porta fornita da Render
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask app on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)

