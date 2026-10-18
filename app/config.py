"""Explicit defaults; settings are read when the factory is called."""
import os
import shutil
from pathlib import Path


def settings(instance):
    def integer(name, default):
        return int(os.getenv(name, default))
    return dict(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY"),
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "postgresql+psycopg://rag:rag@127.0.0.1:55438/rag"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DATA_DIR=Path(instance),
        GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", ""),
        EMBEDDING_MODEL=os.getenv("EMBEDDING_MODEL", "gemini-embedding-001"),
        GENERATION_MODEL=os.getenv("GENERATION_MODEL", "gemini-3.5-flash-lite"),
        EMBEDDING_DIMENSIONS=integer("EMBEDDING_DIMENSIONS", 768),
        EMBEDDING_BATCH_SIZE=integer("EMBEDDING_BATCH_SIZE", 16),
        CHUNK_SIZE=integer("CHUNK_SIZE", 1800),
        CHUNK_OVERLAP=integer("CHUNK_OVERLAP", 250),
        TOP_K=integer("TOP_K", 5),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        PDF_MAX_PAGES=100,
        DOCX_MAX_EXPANDED=100 * 1024 * 1024,
        OCR_MIN_CHARACTERS=40,
        OCR_TIMEOUT=60,
        PROCESS_TIMEOUT=900,
        CONVERT_TIMEOUT=120,
        TESSERACT_CMD=os.getenv("TESSERACT_CMD", shutil.which("tesseract") or ("C:/Program Files/Tesseract-OCR/tesseract.exe" if Path("C:/Program Files/Tesseract-OCR/tesseract.exe").exists() else "tesseract")),
        TESSDATA_PREFIX=os.getenv("TESSDATA_PREFIX", str(Path(instance) / "tessdata") if (Path(instance) / "tessdata").exists() else ""),
        OCR_LANGUAGES=os.getenv("OCR_LANGUAGES", "mon+eng"),
        LIBREOFFICE_CMD=os.getenv("LIBREOFFICE_CMD", shutil.which("soffice") or ("C:/Program Files/LibreOffice/program/soffice.exe" if Path("C:/Program Files/LibreOffice/program/soffice.exe").exists() else "soffice")),
        API_TIMEOUT=30,
        CHAT_TIMEOUT=90,
        CONTEXT_TOKENS=8000,
        HISTORY_TOKENS=2000,
        INPUT_TOKENS=12000,
        OUTPUT_TOKENS=1000,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
    )


def validate(config):
    if not 0 <= config["CHUNK_OVERLAP"] < config["CHUNK_SIZE"]:
        raise ValueError("CHUNK_OVERLAP must be nonnegative and smaller than CHUNK_SIZE")
    if not 128 <= config["EMBEDDING_DIMENSIONS"] <= 3072:
        raise ValueError("EMBEDDING_DIMENSIONS must be 128..3072")
    if not 1 <= config["EMBEDDING_BATCH_SIZE"] <= 100:
        raise ValueError("EMBEDDING_BATCH_SIZE must be 1..100")
    if config["TOP_K"] not in (1, 3, 5, 10):
        raise ValueError("TOP_K must be 1, 3, 5, or 10")
