import os
import json
import math
import requests
from flask import Flask, request, render_template_string, redirect, url_for, session, flash


app = Flask(__name__)
app.secret_key = "314a159d265a358m932k"  




GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

GEMINI_MODEL = "gemini-2.0-flash"



GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"


def generate_questions_with_gemini(program_text: str, num_questions: int):
    """
    Usa l'API di Gemini per generare un set di domande
    a partire dal testo del programma incollato dall'utente.
    Restituisce una lista di dict con chiavi: text, qtype, options, answer.
    """

    if not GEMINI_API_KEY or GEMINI_API_KEY.startswith("INSERISCI_"):
        raise RuntimeError(
            "Devi impostare la GEMINI_API_KEY nel codice o come variabile d'ambiente."
        )

    if num_questions <= 0:
        raise ValueError("Il numero di domande deve essere > 0")


    desired_mcq = math.ceil(num_questions * 2 / 3)
    desired_open = num_questions - desired_mcq

    full_prompt = f"""
Sei un generatore di quiz di fisica in italiano per studenti universitari.
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

    # Estrai il testo generato da Gemini
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Risposta di Gemini inattesa: {e}\n\n{data}")

    text = text.strip()

   
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # fallback: estrai solo il blocco tra { e }
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




INDEX_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <title>Generatore Quiz Fisica (Gemini)</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    textarea { width: 100%; }
    .container { max-width: 900px; margin: auto; }
    .field { margin-bottom: 15px; }
    label { font-weight: bold; }
    input[type="number"] { width: 80px; }
    .error { color: red; }
  </style>
</head>
<body>
<div class="container">
  <h1>Generatore Quiz Fisica – Onde &amp; Acustica (Gemini)</h1>
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <ul class="error">
      {% for msg in messages %}
        <li>{{ msg }}</li>
      {% endfor %}
      </ul>
    {% endif %}
  {% endwith %}

  <form method="post" action="{{ url_for('generate_quiz') }}">
    <div class="field">
      <label for="program_text">Contenuto del programma (testo da studiare):</label><br>
      <textarea id="program_text" name="program_text" rows="15" required></textarea>
    </div>
    <div class="field">
      <label for="num_questions">Numero di domande:</label>
      <input type="number" id="num_questions" name="num_questions" value="31" min="1" max="50" required>
      <small>(circa 2/3 a scelta multipla, 1/3 a completamento)</small>
    </div>
    <button type="submit">Genera quiz</button>
  </form>
</div>
</body>
</html>
"""

QUIZ_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <title>Quiz Fisica – Domande</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    .container { max-width: 900px; margin: auto; }
    .question { margin-bottom: 20px; padding: 10px; border: 1px solid #ccc; border-radius: 5px; }
    .q-text { font-weight: bold; margin-bottom: 5px; }
    .options { margin-left: 15px; }
    .options label { display: block; }
    input[type="text"] { width: 60%; }
  </style>
</head>
<body>
<div class="container">
  <h1>Quiz Fisica – Domande</h1>
  <form method="post" action="{{ url_for('submit_quiz') }}">
    {% for q in questions %}
      {% set qi = loop.index0 %}
      <div class="question">
        <div class="q-text">Domanda {{ loop.index }}:</div>
        <div>{{ q.text }}</div>
        <div class="options">
          {% if q.qtype == "mcq" %}
            {% for opt in q.options %}
              <label>
                <input type="radio" name="q{{ qi }}" value="{{ opt }}">
                {{ opt }}
              </label>
            {% endfor %}
          {% else %}
            <input type="text" name="q{{ qi }}" placeholder="Risposta (una sola parola)">
          {% endif %}
        </div>
      </div>
    {% endfor %}
    <button type="submit">Invia risposte e calcola punteggio</button>
  </form>
</div>
</body>
</html>
"""


RESULT_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <title>Risultato Quiz</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    .container { max-width: 900px; margin: auto; }
    .summary { margin-bottom: 20px; }
    .correct { color: green; }
    .wrong { color: red; }
    .blank { color: gray; }
    .question { margin-bottom: 15px; padding: 10px; border: 1px solid #ccc; border-radius: 5px; }
  </style>
</head>
<body>
<div class="container">
  <h1>Risultato Quiz</h1>
  <div class="summary">
    <p>Domande totali: {{ total }}</p>
    <p class="correct">Corrette: {{ correct }}</p>
    <p class="wrong">Sbagliate: {{ wrong }}</p>
    <p class="blank">Non risposte: {{ blank }}</p>
    <h2>Punteggio finale: {{ score }}</h2>
  </div>

  <h3>Dettaglio domande</h3>
  {% for item in details %}
    <div class="question">
      <div><strong>Domanda {{ loop.index }}:</strong> {{ item.text }}</div>
      <div>Tua risposta:
        {% if item.user_answer is none %}
          <span class="blank">Non data</span>
        {% else %}
          {{ item.user_answer }}
        {% endif %}
      </div>
      <div>Risposta corretta: <strong>{{ item.correct_answer }}</strong></div>
      <div>
        Esito:
        {% if item.result == "correct" %}
          <span class="correct">Corretta (+1)</span>
        {% elif item.result == "wrong" %}
          <span class="wrong">Sbagliata (-0.25)</span>
        {% else %}
          <span class="blank">Non data (0)</span>
        {% endif %}
      </div>
    </div>
  {% endfor %}

  <p><a href="{{ url_for('index') }}">Torna alla pagina iniziale</a></p>
</div>
</body>
</html>
"""



@app.route("/", methods=["GET"])
def index():
    return render_template_string(INDEX_TEMPLATE)


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
    return render_template_string(QUIZ_TEMPLATE, questions=questions)


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

    return render_template_string(
        RESULT_TEMPLATE,
        total=total,
        correct=correct,
        wrong=wrong,
        blank=blank,
        score=score_str,
        details=details,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
