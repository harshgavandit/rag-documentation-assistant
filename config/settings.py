"""Central configuration; environment values override the project's .env file."""
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DOCS_PATH = str(PROJECT_ROOT / os.getenv("DOCS_PATH", "docs"))
CHROMA_PATH = str(PROJECT_ROOT / os.getenv("CHROMA_PATH", "chroma_data"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "rag_docs")
CHAR_COLLECTION_NAME = COLLECTION_NAME + "_character"
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
CHAR_CHUNK_SIZE = 500
CHAR_CHUNK_OVERLAP = 50
SEMANTIC_MAX_WORDS = 400
TOP_K = int(os.getenv("TOP_K", "5"))
RRF_CONSTANT = 60

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna").strip()
OPENAI_MAX_COMPLETION_TOKENS = int(os.getenv("OPENAI_MAX_COMPLETION_TOKENS", "2048"))
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "0"))
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "20"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_PREFIX = os.getenv("RATE_LIMIT_PREFIX", "rag:rate-limit")
REDIS_TIMEOUT_SECONDS = float(os.getenv("REDIS_TIMEOUT_SECONDS", "10"))
REFUSAL_TEXT = "I could not find an answer to this question in the provided documentation."


def validate_configuration() -> None:
    """Reject missing credentials or invalid limits without exposing secret values."""
    missing = [name for name, value in (
        ("OPENAI_API_KEY", OPENAI_API_KEY),
        ("OPENAI_MODEL", OPENAI_MODEL),
        ("UPSTASH_REDIS_REST_URL", UPSTASH_REDIS_REST_URL),
        ("UPSTASH_REDIS_REST_TOKEN", UPSTASH_REDIS_REST_TOKEN),
    ) if not value]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    parsed = urlparse(UPSTASH_REDIS_REST_URL)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("UPSTASH_REDIS_REST_URL must be an HTTPS URL without embedded credentials.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("UPSTASH_REDIS_REST_URL must be the base REST endpoint.")
    if min(TOP_K, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS,
           OPENAI_MAX_COMPLETION_TOKENS, OPENAI_TIMEOUT_SECONDS, REDIS_TIMEOUT_SECONDS) <= 0:
        raise ValueError("Retrieval, token, rate-limit and timeout settings must be positive.")
    if OPENAI_MAX_RETRIES < 0 or not RATE_LIMIT_PREFIX.strip():
        raise ValueError("Retries must be nonnegative and RATE_LIMIT_PREFIX must not be empty.")
