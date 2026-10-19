"""Explicit defaults; settings are read when the factory is called."""
import os
import shutil
from pathlib import Path


def settings(instance):
    def integer(name, default):
        return int(os.getenv(name, default))
    selected_provider = os.getenv("AI_PROVIDER", "gemini").casefold()
    legacy = "AI_PROVIDER" not in os.environ
    gemini_embedding = os.getenv("GEMINI_EMBEDDING_MODEL", os.getenv("EMBEDDING_MODEL", "gemini-embedding-001"))
    gemini_generation = os.getenv("GEMINI_GENERATION_MODEL", os.getenv("GENERATION_MODEL", "gemini-3.5-flash-lite"))
    gemini_dimensions = integer("GEMINI_EMBEDDING_DIMENSIONS", os.getenv("EMBEDDING_DIMENSIONS", "768"))
    openai_embedding = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    openai_generation = os.getenv("OPENAI_GENERATION_MODEL", "gpt-6-luna")
    openai_dimensions = integer("OPENAI_EMBEDDING_DIMENSIONS", 1536)
    # Keep old deployments on Gemini and honor their generic model variables.
    if legacy or selected_provider == "gemini":
        embedding_model, generation_model, dimensions = gemini_embedding, gemini_generation, gemini_dimensions
    else:
        embedding_model, generation_model, dimensions = openai_embedding, openai_generation, openai_dimensions
    return dict(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY"),
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "postgresql+psycopg://rag:rag@127.0.0.1:55438/rag"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DATA_DIR=Path(instance),
        AI_PROVIDER=selected_provider,
        GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", ""),
        GEMINI_EMBEDDING_MODEL=gemini_embedding,
        GEMINI_GENERATION_MODEL=gemini_generation,
        GEMINI_EMBEDDING_DIMENSIONS=gemini_dimensions,
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        OPENAI_EMBEDDING_MODEL=openai_embedding,
        OPENAI_GENERATION_MODEL=openai_generation,
        OPENAI_EMBEDDING_DIMENSIONS=openai_dimensions,
        OPENAI_REASONING_EFFORT=os.getenv("OPENAI_REASONING_EFFORT", "none"),
        EMBEDDING_MODEL=embedding_model,
        GENERATION_MODEL=generation_model,
        EMBEDDING_DIMENSIONS=dimensions,
        EMBEDDING_BATCH_SIZE=integer("EMBEDDING_BATCH_SIZE", 16),
        CHUNK_SIZE=integer("CHUNK_SIZE", 1800),
        CHUNK_OVERLAP=integer("CHUNK_OVERLAP", 250),
        TOP_K=integer("TOP_K", 5),
        RETRIEVAL_MODE=os.getenv("RETRIEVAL_MODE", "hybrid"),
        DENSE_TOP_K=integer("DENSE_TOP_K", 15),
        SPARSE_TOP_K=integer("SPARSE_TOP_K", 15),
        RRF_K=integer("RRF_K", 60),
        RERANKING_ENABLED=os.getenv("RERANKING_ENABLED", "false").casefold() == "true",
        RERANKER_MODEL_PATH=os.getenv("RERANKER_MODEL_PATH", ""),
        QUERY_REWRITING_ENABLED=os.getenv("QUERY_REWRITING_ENABLED", "false").casefold() == "true",
        CONTEXT_CHUNKS=integer("CONTEXT_CHUNKS", 6),
        LANGUAGE_DETECTION_ENABLED=True,
        ROMANIZED_MONGOLIAN_ENABLED=True,
        OUTPUT_LANGUAGE="mn-Cyrl",
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
    if config.get("AI_PROVIDER", "gemini") not in ("openai", "gemini"):
        raise ValueError("AI_PROVIDER must be openai or gemini")
    if config.get("OPENAI_REASONING_EFFORT", "none") not in ("none", "low", "medium", "high", "xhigh", "max"):
        raise ValueError("OPENAI_REASONING_EFFORT is not supported")
    if config["RETRIEVAL_MODE"] not in ("dense", "hybrid"):
        raise ValueError("RETRIEVAL_MODE must be dense or hybrid")
    if config["DENSE_TOP_K"] < 1 or config["SPARSE_TOP_K"] < 1 or config["RRF_K"] < 1 or config["CONTEXT_CHUNKS"] < 1:
        raise ValueError("Retrieval candidate and context limits must be positive")
    if config["RERANKING_ENABLED"] and not config["RERANKER_MODEL_PATH"]:
        raise ValueError("Set RERANKER_MODEL_PATH to a local model directory when reranking is enabled")
    if not 0 <= config["CHUNK_OVERLAP"] < config["CHUNK_SIZE"]:
        raise ValueError("CHUNK_OVERLAP must be nonnegative and smaller than CHUNK_SIZE")
    if not 128 <= config["EMBEDDING_DIMENSIONS"] <= 3072:
        raise ValueError("EMBEDDING_DIMENSIONS must be 128..3072")
    if not 1 <= config["EMBEDDING_BATCH_SIZE"] <= 100:
        raise ValueError("EMBEDDING_BATCH_SIZE must be 1..100")
    if config["TOP_K"] not in (1, 3, 5, 10):
        raise ValueError("TOP_K must be 1, 3, 5, or 10")
