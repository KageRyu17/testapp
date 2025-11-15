import os
import json
import math
import textwrap
import requests
from PyPDF2 import PdfReader
from flask import Flask, request, render_template, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_API_KEY", "super-secret-key")  # fallback di sicurezza

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
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # pragma: no cover - PyPDF2 error paths
            text = ""
        text = text.strip()
        if text:
            extracted_parts.append(text)

    combined = "\n\n".join(extracted_parts).strip()

    if not combined:
        raise ValueError("Non sono riuscito a estrarre testo dal PDF caricato.")

    return combined


def _parse_questions_json(text: str, num_questions: int):
    """
    Parsa il testo restituito da Gemini e ne estrae la lista di domande.
    Ritorna una lista di dict: {text, qtype, options, answer}.
    """

    # Togli eventuali ```json ... ``` / ``` ... ```
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        candidate = None
        for p in parts:
            if "{" in p and "}" in p:
                candidate = p
                break
        if candidate:
            text = candidate.strip()

    # Prendo dal primo "{" all'ultima "}"
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or start > end:
        raise RuntimeError(f"Non trovo JSON valido nella risposta del modello:\n{text[:500]}")

    json_str = text[start : end + 1]

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON di Gemini non valido: {e}\n\nContenuto:\n{json_str[:500]}")

    if not isinstance(data, dict) or "questions" not in data:
        raise RuntimeError(f"Struttura JSON inattesa: manca 'questions':\n{data}")

    raw_questions = data.get("questions", [])
    if not isinstance(raw_questions, list):
        raise RuntimeError("Il campo 'questions' non è una lista.")

    questions = []
    for item in raw_questions:
        if not isinstance(item, dict):
            continue

        text_q = str(item.get("text", "")).strip()
        qtype = str(item.get("qtype", "")).strip().lower()
        options = item.get("options", None)
        answer = str(item.get("answer", "")).strip()

        if not text_q or qtype not in {"mcq", "open"} or not answer:
            continue

        if qtype == "mcq":
            if not isinstance(options, list):
                continue
            cleaned_options = [str(o).strip() for o in options if str(o).strip()]
            if len(cleaned_options) < 2:
                continue
            # risposta deve essere una delle opzioni
            if answer not in cleaned_options:
                lowered = [o.lower() for o in cleaned_options]
                if answer.lower() in lowered:
                    answer = cleaned_options[lowered.index(answer.lower())]
                else:
                    continue
            options = cleaned_options
        else:  # qtype == "open"
            options = None
            # risposta di una sola parola
            answer = answer.split()[0]

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

    # Tronco se il modello ne ha fatte di più
    if len(questions) > num_questions:
        questions = questions[:num_questions]

    return questions


def generate_questions_with_gemini(program_text: str, num_questions: int):
    """
    Usa l'API di Gemini per generare un set di domande
    a partire dal testo fornito.
    Restituisce una lista di dict: {text, qtype, options, answer}.
    """

    if not GEMINI_API_KEY or GEMINI_API_KEY.startswith("INSERISCI_"):
        raise RuntimeError(
            "Devi impostare GEMINI_API_KEY nel codice o come variabile d'ambiente."
        )

    if num_questions <= 0:
        raise ValueError("Il numero di domande deve essere > 0")

    desired_mcq = math.ceil(num_questions * 0.5)
    desired_open = num_questions - desired_mcq

    full_prompt = textwrap.dedent(
        """
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
    ).format(
        num_questions=num_questions,
        desired_mcq=desired_mcq,
        desired_open=desired_open,
        program_text=program_text,
    )

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
        gemini_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Risposta di Gemini inattesa: {e}\n\n{data}")

    questions = _parse_questions_json(gemini_text, num_questions)
    return questions


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate_quiz():
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
    return render_template("quiz.html", questions=questions)


@app.route("/submit", methods=["POST"])
def submit_quiz():
    questions = session.get("questions")
    if not questions:
        flash("Nessun quiz attivo. Genera prima un quiz.")
        return redirect(url_for("index"))

    # Recupero le risposte dell'utente dal form
    user_answers = {}
    for idx, q in enumerate(questions):
        field_name = f"question_{idx}"
        user_answers[idx] = request.form.get(field_name, "").strip()

    results = []
    num_correct = 0

    for idx, q in enumerate(questions):
        correct_answer = str(q.get("answer", "")).strip()
        given = user_answers.get(idx, "")
        # Confronto case-insensitive
        is_correct = given.lower().strip() == correct_answer.lower().strip()
        if is_correct:
            num_correct += 1

        results.append(
            {
                "question": q,
                "user_answer": given,
                "is_correct": is_correct,
                "correct_answer": correct_answer,
            }
        )

    total = len(questions)
    percent = round((num_correct / total) * 100, 1) if total else 0.0

    score = {
        "total": total,
        "correct": num_correct,
        "percent": percent,
    }

    return render_template("results.html", results=results, score=score)


if __name__ == "__main__":
    # IMPORTANTISSIMO per Render: usa la porta fornita da Render
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask app on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
