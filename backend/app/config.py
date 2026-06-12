"""Centralised configuration loaded from .env in repo root.

We deliberately do NOT import dotenv at import time here; the entrypoint
scripts (run_api.py, run_pipeline.py) call `load_env()` once.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Repository root — perception/, two parents up from backend/app/.
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

# Where the original scanned drawings live. Smetchik drops more PDFs/PNGs here.
DATA_DIR = REPO_ROOT / "data_extracted"

# Where we persist per-drawing artefacts: original PNG, pass-1 facts, pass-2
# final JSON, gap report, and cropped re-ask results.
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

# Hardcoded ГЭСН/ФЕР snippets for MVP. Production: load from XML/DB.
GESN_DB_PATH = REPO_ROOT / "backend" / "data" / "gesn_mvp.json"


def load_env() -> None:
    """Read .env from REPO_ROOT into os.environ if python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
        if ENV_FILE.exists():
            load_dotenv(dotenv_path=str(ENV_FILE), override=False)
    except ImportError:
        # Fall back to whatever the shell already provided.
        pass


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required env var {name!r} is not set. Add it to {ENV_FILE} or your shell."
        )
    return value


def get_gemini_key() -> Optional[str]:
    return os.environ.get("GOOGLE_API_KEY")


def get_ollama_key() -> Optional[str]:
    return os.environ.get("OLLAMA_API_KEY")


# Default model for two-pass extraction. Override via env if you want to A/B.
EXTRACTION_MODEL = os.environ.get("QSMETA_EXTRACTION_MODEL", "gemini-3.5-flash")
# Default model for crop re-ask. Cheap and fast on tiny inputs.
CROPCROP_MODEL = os.environ.get("QSMETA_CROP_MODEL", "gemini-3.1-flash-lite")

# Gemini "media_resolution" enum string. We pass it for full-page extraction.
MEDIA_RESOLUTION = os.environ.get("QSMETA_MEDIA_RESOLUTION", "MEDIA_RESOLUTION_HIGH")

# Padding around bbox when re-asking a crop, in pixels.
CROP_PAD_PX = int(os.environ.get("QSMETA_CROP_PAD_PX", "50"))
