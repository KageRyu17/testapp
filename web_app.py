import os
import json
import math
import textwrap
import requests
from PyPDF2 import PdfReader
from flask import Flask, request, render_template, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_API_KEY") 


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
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Risposta di Gemini inattesa: {e}\n\n{data}")


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
    uploaded_pdf = request.files.get("program_pdf")

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

