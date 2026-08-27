"""Wrapper per gosom/google-maps-scraper (binario Go invocato come subprocess).

Genera un file .xlsx scaricabile con i risultati della query.
Nessun tracking, nessuno storico: one-shot.
"""
import csv
import os
import subprocess
import tempfile
import threading
from datetime import datetime

import pandas as pd

from .. import config
from ..db import create_maps_job, update_maps_job

# Mappa campi UI -> colonne CSV di gosom
FIELD_MAP = {
    "nome": "name",
    "categoria": "category",
    "indirizzo": "address",
    "telefono": "phone",
    "sito_web": "website",
    "email": "email",
    "rating": "rating",
    "recensioni": "reviews",
    "orari": "opening_hours",
    "coordinate": "coordinates",
}
# ⚠️ Output FISSO: tutte le ricerche producono sempre le stesse colonne (richiesta Lorenzo)
DEFAULT_FIELDS = ["categoria", "indirizzo", "telefono", "sito_web"]

# Target lead scelto dall'utente -> depth di scansione gosom
TARGET_TO_DEPTH = {50: 8, 300: 50, 1000: 150, 10000: 400}

# Tutti i campi che gosom può scrivere nel CSV (usati in modalità debug/test)
ALL_FIELDS = ["nome", "categoria", "indirizzo", "telefono", "sito_web", "email", "rating", "recensioni", "orari", "coordinate"]


def _fields_to_csv_cols(campi: list[str]) -> list[str]:
    if not campi:
        campi = DEFAULT_FIELDS
    cols = [FIELD_MAP[c] for c in campi if c in FIELD_MAP]
    return cols or ["name"]


def _count_rows(csv_path: str) -> int:
    """Conta le righe dati (escluso header) di un CSV gosom parziale."""
    try:
        if not os.path.exists(csv_path):
            return 0
        with open(csv_path, newline="", errors="ignore") as f:
            return max(0, sum(1 for _ in f) - 1)
    except Exception:
        return 0


def run_maps_job(job_id: int, query: str, campi: list[str], target: int = 0, keep_all: bool = False):
    """Esegue gosom in background e produce l'xlsx. Da lanciare in un thread.
    Di default l'output è FISSO (DEFAULT_FIELDS); con keep_all=True (solo debug/test)
    mantiene TUTTE le colonne che gosom produce per capire cosa si compila davvero."""
    update_maps_job(job_id, stato="in_corso", trovati=0)
    filename = None
    campi = ALL_FIELDS if keep_all else DEFAULT_FIELDS  # output fisso oppure tutti i campi (debug)
    try:
        if not os.path.exists(config.GMAPS_BINARY):
            raise RuntimeError(f"Binario gosom non trovato: {config.GMAPS_BINARY}")

        depth = TARGET_TO_DEPTH.get(target, config.GMAPS_DEPTH if target <= 0 else min(400, max(8, target // 5)))
        with tempfile.TemporaryDirectory() as tmp:
            queries_file = os.path.join(tmp, "queries.txt")
            results_csv = os.path.join(tmp, "results.csv")
            with open(queries_file, "w") as f:
                f.write(query + "\n")

            cmd = [
                config.GMAPS_BINARY,
                "-input", queries_file,
                "-results", results_csv,
                "-depth", str(depth),
                "-c", str(config.GMAPS_CONCURRENCY),
                "-exit-on-inactivity", f"{config.GMAPS_TIMEOUT_MIN}m",
            ]
            if "email" in [FIELD_MAP.get(c) for c in campi]:
                cmd.append("-email")
            if config.PROXY_URL:
                cmd += ["-proxies", config.PROXY_URL]

            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # monitora il progresso: aggiorna "trovati" ogni 4s finché gosom gira
            import time
            deadline = time.time() + config.GMAPS_TIMEOUT_MIN * 60 + 120
            last = -1
            while proc.poll() is None:
                time.sleep(4)
                n = _count_rows(results_csv)
                if n != last:
                    update_maps_job(job_id, trovati=n)
                    last = n
                if time.time() > deadline:
                    proc.kill()
                    raise RuntimeError("timeout gosom")
            out, err = proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"gosom exit {proc.returncode}: {(err or out)[-600:]}")
            if not os.path.exists(results_csv):
                raise RuntimeError(f"gosom non ha prodotto output. {proc.stderr[-500:]}")
            if os.path.getsize(results_csv) == 0:
                raise RuntimeError(f"gosom ha prodotto un CSV vuoto. stderr: {proc.stderr[-500:]}")

            update_maps_job(job_id, trovati=_count_rows(results_csv))
            df = pd.read_csv(results_csv, dtype=str)
            # mappa le colonne CSV ai nomi italiani richiesti
            rename = {v: k for k, v in FIELD_MAP.items()}
            df = df.rename(columns={c: rename.get(c, c) for c in df.columns})
            keep = [rename.get(c, c) for c in _fields_to_csv_cols(campi)]
            keep = [k for k in keep if k in df.columns]
            df = df[keep] if keep else df

            out_dir = config.DATA_DIR / "downloads"
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"gmaps_{ts}.xlsx"
            df.to_excel(out_dir / filename, index=False, engine="openpyxl")

        update_maps_job(job_id, stato="completato", filename=filename, completato_il=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        update_maps_job(job_id, stato="errore", completato_il=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        from ..db import add_log
        add_log("errore", f"Job Maps '{query}': {e}")
