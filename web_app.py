import os
import json
import math
import requests
from flask import Flask, request, render_template, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_API_KEY") 


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "INSERISCI_LA_TUA_GEMINI_API_KEY_QUI")


GEMINI_MODEL = "gemini-2.0-flash"


GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)


def generate_questions_with_gemini(program_text: str, num_questions: int):
    """
    Usa l'API di Gemini per generare un set di domande
    a partire dal testo incollato dall’utente.
    Restituisce una lista di dict: {text, qtype, options, answer}.
    """

    if not GEMINI_API_KEY or GEMINI_API_KEY.startswith("INSERISCI_"):
        raise RuntimeError(
            "Devi impostare GEMINI_API_KEY nel codice o come variabile d'ambiente."
        )

    if num_questions <= 0:
        raise ValueError("Il numero di domande deve essere > 0")


    desired_mcq = math.ceil(num_questions * 2 / 3)
    desired_open = num_questions - desired_mcq

    full_prompt = f"""
Sei un generatore di quiz in italiano per studenti universitari.
Devi creare domande a partire dal testo fornito (contenuto del programma).

PROMPT DIDATTICO DI BASE:
"Questo è il contenuto del primo punto del programma che devo studiare.
Fai domande in modo che io sappia bene la teoria.
Non chiedermi formule, quelle le studio io da solo.
Fai quiz a risposta multipla e quiz a completamento con una sola parola."

OBIETTIVO:
- Genera ESATTAMENTE {num_questions} domande.
- Circa {desired_mcq} domande devono essere a risposta multipla (mcq).
- Circa {desired_open} domande devono essere a completamento con una sola parola (open).
- Le domande devono essere in italiano.
- NON fare domande con formule o calcoli, solo concetti teorici.

FORMATO DI USCITA:
Devi restituire ESCLUSIVAMENTE un oggetto JSON con questa struttura:

{{
  "questions": [
    {{
      "text": "testo della domanda",
      "qtype": "mcq" oppure "open",
      "options": ["opzione 1", "opzione 2", "opzione 3", "opzione 4"] oppure null,
      "answer": "testo della risposta corretta"
    }},
    ...
  ]
}}

Regole:
- Se "qtype" è "mcq":
  - "options" deve essere una lista di 3 o 4 stringhe.
  - "answer" deve essere ESATTAMENTE una delle stringhe in "options".
- Se "qtype" è "open":
  - "options" deve essere null.
  - "answer" deve essere UNA sola parola (la soluzione che lo studente deve scrivere).
- Non aggiungere testo fuori dal JSON.

Numero di domande richieste: {num_questions}

--- INIZIO TESTO PROGRAMMA ---
{program_text}
--- FINE TESTO PROGRAMMA ---
"""

    payload = {
        "contents": [
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
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Risposta di Gemini inattesa: {e}\n\n{data}")

    text = text.strip()


    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError("Gemini non ha restituito un JSON valido.")
        fragment = text[start:end + 1]
        parsed = json.loads(fragment)

    if "questions" not in parsed or not isinstance(parsed["questions"], list):
        raise RuntimeError("JSON valido ma senza campo 'questions' nel formato atteso.")

    questions = []
    for item in parsed["questions"]:
        text_q = item.get("text")
        qtype = item.get("qtype")
        options = item.get("options")
        answer = item.get("answer")

        if not text_q or not qtype or answer is None:
            continue
        if qtype not in ("mcq", "open"):
            continue

        if qtype == "open":
            options = None
        else:
            if not isinstance(options, list) or len(options) == 0:
                continue

        questions.append(
            {
                "text": text_q,
                "qtype": qtype,
                "options": options,
                "answer": answer,
            }
        )

    if not questions:
        raise RuntimeError("Nessuna domanda valida generata dal modello.")

    if len(questions) > num_questions:
        questions = questions[:num_questions]

    return questions




@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate_quiz():
    program_text = request.form.get("program_text", "").strip()
    num_questions_str = request.form.get("num_questions", "").strip()

    if not program_text:
        flash("Devi incollare il contenuto del programma.")
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
    return render_template("quiz.html", questions=questions)


@app.route("/submit", methods=["POST"])
def submit_quiz():
    questions = session.get("questions")
    if not questions:
        flash("Nessun quiz attivo. Genera prima un quiz.")
        return redirect(url_for("index"))

    score = 0.0
    correct = 0
    wrong = 0
    blank = 0
    details = []

    for i, q in enumerate(questions):
        field_name = f"q{i}"
        ans = request.form.get(field_name, "").strip()
        user_answer = ans if ans else None

        if not user_answer:
            blank += 1
            result = "blank"
        else:
            if q["qtype"] == "mcq":
                if user_answer == q["answer"]:
                    score += 1.0
                    correct += 1
                    result = "correct"
                else:
                    score -= 0.25
                    wrong += 1
                    result = "wrong"
            else:  # open
                if user_answer.lower() == q["answer"].lower():
                    score += 1.0
                    correct += 1
                    result = "correct"
                else:
                    score -= 0.25
                    wrong += 1
                    result = "wrong"

        details.append(
            {
                "text": q["text"],
                "user_answer": user_answer,
                "correct_answer": q["answer"],
                "result": result,
            }
        )

    total = len(questions)
    score_str = f"{score:.2f}"

    session.pop("questions", None)

    return render_template(
        "result.html",
        total=total,
        correct=correct,
        wrong=wrong,
        blank=blank,
        score=score_str,
        details=details,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

