"""Layer SQLite — configurazione job, stato annunci, log esecuzioni."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from .config import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    portale TEXT NOT NULL,              -- 'idealista' | 'immobiliare'
    url_ricerca TEXT NOT NULL,
    filtri TEXT DEFAULT '{}',           -- JSON: comune, tipologia, prezzo_min/max, mq_min
    frequenza_ore REAL DEFAULT 24,
    campi TEXT DEFAULT '{}',            -- JSON: checkbox campi selezionati
    stato TEXT DEFAULT 'attivo',        -- attivo | pausa
    sheet_id TEXT,
    sheet_url TEXT,
    ultima_esecuzione TEXT,
    esito_ultima TEXT,
    max_pages INTEGER DEFAULT 0,        -- 0 = usa il default config; altrimenti pagine max
    progresso TEXT DEFAULT '',          -- JSON live: {"pagina":N,"totale":M,"trovati":K}
    creato_il TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS annunci (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id INTEGER NOT NULL,
    annuncio_id TEXT NOT NULL,          -- ID univoco estratto dall'URL
    url TEXT NOT NULL,
    dati TEXT DEFAULT '{}',             -- JSON: tutti i campi estratti
    prezzo_iniziale REAL,
    prezzo_attuale REAL,
    num_ribassi INTEGER DEFAULT 0,
    data_primo_avvistamento TEXT,
    data_ultimo_avvistamento TEXT,
    giorni_online INTEGER,
    stato TEXT DEFAULT 'attivo',        -- attivo | venduto-rimosso
    data_scomparsa TEXT,
    UNIQUE(monitor_id, annuncio_id)
);
CREATE TABLE IF NOT EXISTS ribassi (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id INTEGER NOT NULL,
    annuncio_id TEXT NOT NULL,
    url TEXT,
    data TEXT,
    prezzo_vecchio REAL,
    prezzo_nuovo REAL,
    pct REAL
);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (datetime('now')),
    livello TEXT DEFAULT 'info',        -- info | errore
    monitor_id INTEGER,
    messaggio TEXT
);
CREATE TABLE IF NOT EXISTS credit_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL,                 -- 'zenrows' | 'gmaps'
    quantita INTEGER DEFAULT 1,
    creato_il TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS jobs_maps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    campi TEXT DEFAULT '{}',
    stato TEXT DEFAULT 'in_coda',       -- in_coda | in_corso | completato | errore
    filename TEXT,
    target INTEGER DEFAULT 0,           -- lead richiesti (0 = nessun limite)
    trovati INTEGER DEFAULT 0,          -- risultati trovati finora (progresso live)
    creato_il TEXT DEFAULT (datetime('now')),
    completato_il TEXT
);
"""


def _migrate(conn):
    """Aggiunge colonne mancanti su DB esistenti (migrazione leggera)."""
    mcols = {r[1] for r in conn.execute("PRAGMA table_info(monitors)").fetchall()}
    if "max_pages" not in mcols:
        conn.execute("ALTER TABLE monitors ADD COLUMN max_pages INTEGER DEFAULT 0")
    if "progresso" not in mcols:
        conn.execute("ALTER TABLE monitors ADD COLUMN progresso TEXT DEFAULT ''")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs_maps)").fetchall()}
    if "target" not in cols:
        conn.execute("ALTER TABLE jobs_maps ADD COLUMN target INTEGER DEFAULT 0")
    if "trovati" not in cols:
        conn.execute("ALTER TABLE jobs_maps ADD COLUMN trovati INTEGER DEFAULT 0")
    # Google Maps (gosom) NON consuma crediti: rimuovi eventuali record residui
    try:
        conn.execute("DELETE FROM credit_usage WHERE tipo='gmaps'")
    except Exception:
        pass


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


@contextmanager
def db():
    conn = get_conn()
    try:
        with _lock:
            yield conn
            conn.commit()
    finally:
        conn.close()


# ---------- MONITOR ----------
def create_monitor(nome, portale, url_ricerca, filtri=None, frequenza_ore=24, campi=None, max_pages=0):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO monitors (nome, portale, url_ricerca, filtri, frequenza_ore, campi, max_pages) VALUES (?,?,?,?,?,?,?)",
            (nome, portale, url_ricerca, json.dumps(filtri or {}), frequenza_ore, json.dumps(campi or {}), max_pages),
        )
        return cur.lastrowid


def get_monitor(monitor_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM monitors WHERE id=?", (monitor_id,)).fetchone()
        return dict(row) if row else None


def list_monitors():
    with db() as conn:
        rows = conn.execute("SELECT * FROM monitors ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def update_monitor(monitor_id, **fields):
    allowed = {"nome", "portale", "url_ricerca", "filtri", "frequenza_ore", "campi", "stato", "sheet_id", "sheet_url", "ultima_esecuzione", "esito_ultima", "max_pages", "progresso"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    vals.append(monitor_id)
    with db() as conn:
        conn.execute(f"UPDATE monitors SET {', '.join(sets)} WHERE id=?", vals)


def delete_monitor(monitor_id):
    with db() as conn:
        conn.execute("DELETE FROM monitors WHERE id=?", (monitor_id,))
        conn.execute("DELETE FROM annunci WHERE monitor_id=?", (monitor_id,))
        conn.execute("DELETE FROM ribassi WHERE monitor_id=?", (monitor_id,))


# ---------- ANNUNCI ----------
def get_annuncio(monitor_id, annuncio_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM annunci WHERE monitor_id=? AND annuncio_id=?", (monitor_id, annuncio_id)).fetchone()
        return dict(row) if row else None


def list_annunci(monitor_id, solo_attivi=False):
    q = "SELECT * FROM annunci WHERE monitor_id=?"
    args = [monitor_id]
    if solo_attivi:
        q += " AND stato='attivo'"
    q += " ORDER BY id DESC"
    with db() as conn:
        return [dict(r) for r in conn.execute(q, args).fetchall()]


def upsert_annuncio(monitor_id, annuncio_id, url, dati, prezzo):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        row = conn.execute("SELECT * FROM annunci WHERE monitor_id=? AND annuncio_id=?", (monitor_id, annuncio_id)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO annunci (monitor_id, annuncio_id, url, dati, prezzo_iniziale, prezzo_attuale, data_primo_avvistamento, data_ultimo_avvistamento, stato) VALUES (?,?,?,?,?,?,?,?,'attivo')",
                (monitor_id, annuncio_id, url, json.dumps(dati), prezzo, prezzo, now, now),
            )
            return {"esito": "nuovo"}
        # aggiorna: data ultimo avvistamento + eventuale ribasso
        vecchio = row["prezzo_attuale"]
        conn.execute(
            "UPDATE annunci SET dati=?, prezzo_attuale=?, data_ultimo_avvistamento=? WHERE id=?",
            (json.dumps(dati), prezzo, now, row["id"]),
        )
        if vecchio is not None and prezzo is not None and prezzo < vecchio:
            conn.execute(
                "UPDATE annunci SET num_ribassi=num_ribassi+1 WHERE id=?", (row["id"],)
            )
            conn.execute(
                "INSERT INTO ribassi (monitor_id, annuncio_id, url, data, prezzo_vecchio, prezzo_nuovo, pct) VALUES (?,?,?,?,?,?,?)",
                (monitor_id, annuncio_id, url, now, vecchio, prezzo, round((vecchio - prezzo) / vecchio * 100, 1)),
            )
            return {"esito": "ribasso", "prezzo_vecchio": vecchio, "prezzo_nuovo": prezzo}
        return {"esito": "presente"}


def mark_scomparso(monitor_id, annuncio_id, data_scomparsa):
    with db() as conn:
        row = conn.execute("SELECT * FROM annunci WHERE monitor_id=? AND annuncio_id=?", (monitor_id, annuncio_id)).fetchone()
        if row and row["stato"] == "attivo":
            primo = row["data_primo_avvistamento"] or data_scomparsa
            giorni = 0
            try:
                giorni = (datetime.strptime(data_scomparsa, "%Y-%m-%d %H:%M:%S") - datetime.strptime(primo, "%Y-%m-%d %H:%M:%S")).days
            except Exception:
                pass
            conn.execute(
                "UPDATE annunci SET stato='venduto-rimosso', data_scomparsa=?, giorni_online=? WHERE id=?",
                (data_scomparsa, max(0, giorni), row["id"]),
            )
            return {"annuncio_id": annuncio_id, "giorni_online": max(0, giorni)}
    return None


# ---------- LOG ----------
def add_log(livello, messaggio, monitor_id=None):
    with db() as conn:
        conn.execute("INSERT INTO log (livello, messaggio, monitor_id) VALUES (?,?,?)", (livello, messaggio, monitor_id))


def list_log(limit=100):
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


# ---------- JOBS MAPS ----------
def create_maps_job(query, campi, target=0):
    with db() as conn:
        cur = conn.execute("INSERT INTO jobs_maps (query, campi, target) VALUES (?,?,?)", (query, json.dumps(campi), target))
        return cur.lastrowid


def update_maps_job(job_id, **fields):
    allowed = {"stato", "filename", "completato_il", "trovati", "target"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    vals.append(job_id)
    with db() as conn:
        conn.execute(f"UPDATE jobs_maps SET {', '.join(sets)} WHERE id=?", vals)


def get_maps_job(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM jobs_maps WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_maps_jobs(limit=50):
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM jobs_maps ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def add_credit_usage(tipo: str, quantita: int = 1):
    """Registra un consumo di credito (zenrows/gmaps)."""
    try:
        with db() as conn:
            conn.execute("INSERT INTO credit_usage (tipo, quantita) VALUES (?,?)", (tipo, quantita))
    except Exception:
        pass


def get_credits_monthly(limite: int) -> dict:
    """Crediti usati questo mese (per tipo e totale) + rimasti."""
    with db() as conn:
        rows = conn.execute(
            "SELECT tipo, SUM(quantita) AS tot FROM credit_usage "
            "WHERE creato_il >= strftime('%Y-%m-01','now') GROUP BY tipo"
        ).fetchall()
        by_type = {r["tipo"]: r["tot"] for r in rows}
    usati = sum(by_type.values())
    return {
        "usati": usati,
        "limite": limite,
        "rimasti": max(0, limite - usati),
        "per_tipo": by_type,
    }
