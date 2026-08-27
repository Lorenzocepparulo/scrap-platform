"""Configurazione centralizzata — tutto parte dalle variabili d'ambiente."""
import os
import base64
import json
from pathlib import Path

DATA_DIR = Path(os.environ.get("SCRAP_DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "scrap.db"

# Scheduler
DEFAULT_FREQUENCY_HOURS = float(os.environ.get("SCRAP_DEFAULT_FREQUENCY_HOURS", "24"))
SCHEDULER_TIMEZONE = os.environ.get("SCRAP_TZ", "Europe/Rome")

# Anti-bot
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]
DELAY_MIN = float(os.environ.get("SCRAP_DELAY_MIN", "2.5"))
DELAY_MAX = float(os.environ.get("SCRAP_DELAY_MAX", "6.0"))
PROXY_URL = os.environ.get("SCRAP_PROXY", "").strip() or None  # es. http://user:pass@host:port
# Gateway anti-bot opzionale (ZenRows-style): l'URL viene appeso a ?url=<target>&apikey=<key>
ANTIBOT_URL = os.environ.get("SCRAP_ANTIBOT_URL", "").strip() or None  # es. https://api.zenrows.com/v1/
ANTIBOT_KEY = os.environ.get("SCRAP_ANTIBOT_KEY", "").strip() or None
MAX_PAGES = int(os.environ.get("SCRAP_MAX_PAGES", "3"))
MAX_ANNUNCI = int(os.environ.get("SCRAP_MAX_ANNUNCI", "60"))

# Auth dashboard
DASHBOARD_PASSWORD = os.environ.get("SCRAP_PASSWORD", "solovera-scrap-2026")
SESSION_SECRET = os.environ.get("SCRAP_SESSION_SECRET", "cambiami-sessione")

# Google Sheets via Maton (nessun service account necessario)
MATON_API_KEY = os.environ.get("MATON_API_KEY", "").strip()
SHEET_SHARE_EMAIL = os.environ.get("SCRAP_SHEET_SHARE_EMAIL", "").strip() or None

# Fallback legacy: service account (solo se un giorno servisse senza Maton)
_sa_raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SERVICE_ACCOUNT = None
if _sa_raw:
    try:
        GOOGLE_SERVICE_ACCOUNT = json.loads(base64.b64decode(_sa_raw).decode()) if _sa_raw.startswith("ey") else json.loads(_sa_raw)
    except Exception:
        try:
            GOOGLE_SERVICE_ACCOUNT = json.loads(Path(_sa_raw).read_text())
        except Exception:
            GOOGLE_SERVICE_ACCOUNT = None

# Google Maps (gosom)
GMAPS_BINARY = os.environ.get("SCRAP_GMAPS_BINARY", "/usr/local/bin/gm_scraper")
GMAPS_DEPTH = int(os.environ.get("SCRAP_GMAPS_DEPTH", "3"))
GMAPS_CONCURRENCY = int(os.environ.get("SCRAP_GMAPS_CONCURRENCY", "2"))
GMAPS_TIMEOUT_MIN = int(os.environ.get("SCRAP_GMAPS_TIMEOUT_MIN", "10"))
