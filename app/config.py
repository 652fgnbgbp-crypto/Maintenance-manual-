"""Runtime configuration, all overridable with environment variables."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("MX_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
FILES_DIR = DATA_DIR / "files"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "library.db"

# Default aircraft type for a new organisation (each organisation can change its own).
AIRCRAFT_TYPE = os.environ.get("MX_AIRCRAFT_TYPE", "Cessna 441 Conquest / Conquest II")
DEFAULT_ORG_NAME = os.environ.get("VANTAGE_ORG_NAME", "My organisation")
PRODUCT_NAME = "Vantage"
COMPANY_NAME = "Torwood Aerospace Group"

# Model used for diagnosis, and for the optional per-page enrichment pass.
DIAGNOSIS_MODEL = os.environ.get("MX_DIAGNOSIS_MODEL", "claude-opus-5-5")
ENRICH_MODEL = os.environ.get("MX_ENRICH_MODEL", "claude-opus-5-5")
DIAGNOSIS_EFFORT = os.environ.get("MX_DIAGNOSIS_EFFORT", "high")
MAX_AGENT_TURNS = int(os.environ.get("MX_MAX_AGENT_TURNS", "24"))
# Yes / No / Unsure questions the AI may ask per diagnosis, and how many more each "Ask 5 more" allows.
QUESTION_LIMIT = 15
QUESTION_EXTENSION = 5
QUESTIONS_PER_BATCH = 5

# Long edge, in pixels, of page images sent to the model (1568 is the model's sweet spot).
MODEL_IMAGE_LONG_EDGE = 1568
# Long edge of images shown in the browser viewer.
VIEW_IMAGE_LONG_EDGE = 2400


def ensure_dirs() -> None:
    for d in (DATA_DIR, FILES_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
