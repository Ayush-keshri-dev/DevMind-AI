"""
DevMind AI - FastAPI backend.

Routes
------
GET  /            -> serves the landing page (index.html)
GET  /chat        -> quick text-only test route, e.g. /chat?question=hello
POST /api/chat    -> text-only chat (JSON body)
POST /api/upload  -> chat with attachments (images, videos, documents, code)

The Google API key lives only here, never in the frontend.
"""

import io
import json
import os
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel

# Read variables from the .env file into the environment.
load_dotenv()

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

# .env defines GEMINI_API_KEY. GOOGLE_API_KEY is accepted too so the project
# works with either name (both are common in Gemini tutorials).
API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("GEMMA_MODEL", "gemma-4-26b-a4b-it")

# Upload limits. Keep MAX_FILES / MAX_FILE_MB in sync with static/script.js
# so the browser warns the user before the server has to reject anything.
MAX_FILES = 5             # attachments allowed per message
MAX_FILE_MB = 15          # size limit for a single file
MAX_TOTAL_MB = 20         # combined limit (Gemini accepts roughly 20 MB inline)
MAX_DOC_CHARS = 20000     # extracted text kept per document
MAX_HISTORY_TURNS = 6     # how much earlier conversation we resend
MAX_HISTORY_CHARS = 2000  # characters kept per history message

# File types are decided by extension, never by the browser-supplied
# content type, because a client can label a file as anything it likes.
IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
    ".json", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".rb", ".php",
    ".xml", ".yml", ".yaml", ".csv", ".sql", ".sh",
}

app = FastAPI(title="DevMind AI")

app.mount("/static", StaticFiles(directory="static"), name="static")

# If the key is missing we do NOT crash the whole server: the website still
# loads and the chat route returns a clear, actionable error instead.
client = genai.Client(api_key=API_KEY) if API_KEY else None


def get_client():
    if client is None:
        raise HTTPException(
            status_code=500,
            detail="No API key found. Add GEMINI_API_KEY to your .env file, then restart the server.",
        )
    return client


# ----------------------------------------------------------------------
# Reading attachments
# ----------------------------------------------------------------------

def truncate(text: str, limit: int = MAX_DOC_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[content truncated]"


def extract_pdf(raw: bytes) -> str:
    """Pull the text out of a PDF using PyMuPDF."""
    import pymupdf

    with pymupdf.open(stream=raw, filetype="pdf") as document:
        return "\n".join(page.get_text() for page in document)


def extract_docx(raw: bytes) -> str:
    """Pull the text out of a Word document using python-docx."""
    from docx import Document

    document = Document(io.BytesIO(raw))
    lines = [paragraph.text for paragraph in document.paragraphs]
    # Tables are not paragraphs, so read them separately.
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(lines)


def extract_text_file(raw: bytes) -> str:
    """Plain text and source code files: decode them, never fail on odd bytes."""
    return raw.decode("utf-8", errors="replace")


# ----------------------------------------------------------------------
# Building the request for the model
# ----------------------------------------------------------------------

def build_question(message: str, history, documents) -> str:
    """
    Turn our conversation + extracted documents into one text prompt.

    `documents` is a list of (filename, text) pairs. Images and videos are not
    text, so they are sent separately as binary parts.
    """
    blocks = []

    if isinstance(history, list) and history:
        turns = []
        for item in history[-MAX_HISTORY_TURNS:]:
            if not isinstance(item, dict):
                continue
            speaker = "User" if item.get("role") == "user" else "DevMind AI"
            content = str(item.get("content", "")).strip()[:MAX_HISTORY_CHARS]
            if content:
                turns.append(f"{speaker}: {content}")
        if turns:
            blocks.append("Earlier conversation:\n" + "\n".join(turns))

    for name, text in documents:
        blocks.append(f"Attached file - {name}:\n{truncate(text)}")

    question = message.strip() or "Please review the attached file(s) and tell me what you find."
    blocks.append("User's current message:\n" + question)
    return "\n\n".join(blocks)


def ask_model(contents) -> str:
    """Send the prepared content to Gemma and return plain text."""
    try:
        response = get_client().models.generate_content(
            model=MODEL_NAME,
            contents=contents,
        )
    except HTTPException:
        raise
    except Exception as exc:  # network error, bad key, model unavailable...
        raise HTTPException(
            status_code=502,
            detail=f"The AI service could not answer: {exc}",
        ) from exc
    return (response.text or "").strip() or "The model returned an empty response."


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

@app.get("/")
def home():
    return FileResponse("index.html")


@app.get("/chat")
def chat(question: str):
    """Simple test route: /chat?question=Explain Python loops"""
    return {"answer": ask_model([question])}


class ChatRequest(BaseModel):
    message: str
    history: Optional[list] = None


@app.post("/api/chat")
def ai_chat(request: ChatRequest):
    """Text-only chat used when the user has not attached any file."""
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Please type a message.")
    prompt = build_question(request.message, request.history, [])
    return {"answer": ask_model([prompt])}


@app.post("/api/upload")
async def chat_with_attachments(
    message: str = Form(""),
    history: str = Form("[]"),
    files: Optional[List[UploadFile]] = File(None),
):
    """
    Chat with attachments. The browser sends multipart form data:
      message -> the typed text
      history -> earlier conversation as a JSON string
      files   -> the attached files
    """
    uploads = files or []

    if not uploads and not message.strip():
        raise HTTPException(status_code=400, detail="Please type a message or attach a file.")

    if len(uploads) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. You can attach up to {MAX_FILES} files at a time.",
        )

    media_parts = []   # binary parts for images and videos
    documents = []     # (filename, extracted text) for PDFs, DOCX and code
    total_bytes = 0

    for upload in uploads:
        name = upload.filename or "file"
        extension = os.path.splitext(name)[1].lower()
        raw = await upload.read()

        if not raw:
            raise HTTPException(status_code=400, detail=f"{name} is empty.")

        if len(raw) > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=f"{name} is larger than {MAX_FILE_MB} MB. Please compress it or send a shorter clip.",
            )

        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_MB * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=f"All attachments together must stay under {MAX_TOTAL_MB} MB.",
            )

        try:
            if extension in IMAGE_MIME:
                # Images go to the model as raw bytes so it can "see" them.
                media_parts.append(types.Part.from_bytes(data=raw, mime_type=IMAGE_MIME[extension]))
            elif extension in VIDEO_MIME:
                # Short videos are sent inline. Large files belong in the Files API,
                # which is a later enhancement.
                media_parts.append(types.Part.from_bytes(data=raw, mime_type=VIDEO_MIME[extension]))
            elif extension == ".pdf":
                documents.append((name, extract_pdf(raw)))
            elif extension == ".docx":
                documents.append((name, extract_docx(raw)))
            elif extension in TEXT_EXTENSIONS:
                documents.append((name, extract_text_file(raw)))
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"{name}: unsupported file type '{extension or 'unknown'}'. "
                           "Use images, videos, PDF, DOCX or text/code files.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not read {name}: {exc}") from exc

    try:
        parsed_history = json.loads(history) if history else []
    except json.JSONDecodeError:
        parsed_history = []

    prompt = build_question(message, parsed_history, documents)

    # Text first, then the media bytes.
    contents = [prompt] + media_parts
    return {"answer": ask_model(contents)}
