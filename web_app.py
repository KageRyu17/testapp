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
        # cerco il blocco che contiene una graffa
        candidate = None
        for p in parts:
            if "{" in p and "}" in p:
                candidate = p
                break
        if candidate:
            text = candidate.strip()

    # Prendo dal primo "{" al
