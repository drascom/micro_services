"""Configuration for diziflix server2, sourced from the environment / .env.

Only the variables of README §6 live here; every other runtime knob is a row of the `settings` table (app/settings.py).
"""
from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "2.0.0-dev"


def _load_dotenv() -> None:
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _path(name: str, default_rel: str) -> str:
    value = os.environ.get(name) or default_rel
    return value if os.path.isabs(value) else os.path.join(BASE_DIR, value)


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = _int("PORT", 8091)

# Everything the server writes lives under DATA_DIR.
DATA_DIR = _path("DATA_DIR", "data")
DB_PATH = os.path.join(DATA_DIR, "diziflix2.db")
IMG_CACHE_DIR = os.path.join(DATA_DIR, "imgcache")
PAGES_DIR = os.path.join(DATA_DIR, "pages")
PROVIDERS_DIR = os.path.join(DATA_DIR, "providers")

# Secrets: never logged, never returned in a response, never put into an LLM prompt.
TMDB_ACCESS_KEY = os.environ.get("TMDB_ACCESS_KEY", "")
STREAM_PROXY_SECRET = os.environ.get("STREAM_PROXY_SECRET", "")

PI_BIN = os.environ.get("PI_BIN", "pi")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
OBSCURA_BIN = os.environ.get("OBSCURA_BIN", "")


def test_sandbox_active() -> bool:
    """True only inside the test sandbox (tests/_sandbox.py): ``DIZIFLIX_TEST_SANDBOX`` names an existing directory under
    the system temp dir AND ``DATA_DIR`` is that same directory. Test-only switches (``DIZIFLIX_FAKE_LLM``, netguard
    test hosts) are ignored otherwise, so a stray env var in production changes nothing."""
    import tempfile
    root = os.environ.get("DIZIFLIX_TEST_SANDBOX") or ""
    data = os.environ.get("DATA_DIR") or ""
    if not root or not data or not os.path.isdir(root):
        return False
    real = os.path.realpath(root)
    tmp = os.path.realpath(tempfile.gettempdir())
    return real == os.path.realpath(data) and (real + os.sep).startswith(tmp + os.sep)
