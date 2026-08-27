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


def _fields_to_csv_cols(campi: list[str]) -> list[str]:
    if not campi:
        campi = DEFAULT_FIELDS
    cols = [FIELD_MAP[c] for c in campi if c in FIELD_MAP]
    return cols or ["name"]


def run_maps_job(job_id: int, query: str, campi: list[str]):
    """Esegue gosom in background e produce l'xlsx. Da lanciare in un thread.
    I campi sono IGNORATI: l'output usa sempre DEFAULT_FIELDS (fisso)."""
    update_maps_job(job_id, stato="in_corso")
    filename = None
    campi = DEFAULT_FIELDS  # output fisso indipendentemente dalla selezione
    try:
        if not os.path.exists(config.GMAPS_BINARY):
            raise RuntimeError(f"Binario gosom non trovato: {config.GMAPS_BINARY}")

        with tempfile.TemporaryDirectory() as tmp:
            queries_file = os.path.join(tmp, "queries.txt")
            results_csv = os.path.join(tmp, "results.csv")
            with open(queries_file, "w") as f:
                f.write(query + "\n")

            cmd = [
                config.GMAPS_BINARY,
                "-input", queries_file,
                "-results", results_csv,
                "-depth", str(config.GMAPS_DEPTH),
                "-c", str(config.GMAPS_CONCURRENCY),
                "-exit-on-inactivity", f"{config.GMAPS_TIMEOUT_MIN}m",
            ]
            if "email" in [FIELD_MAP.get(c) for c in campi]:
                cmd.append("-email")
            if config.PROXY_URL:
                cmd += ["-proxies", config.PROXY_URL]

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=config.GMAPS_TIMEOUT_MIN * 60 + 120)
            if proc.returncode != 0:
                raise RuntimeError(f"gosom exit {proc.returncode}: {proc.stderr[-600:] or proc.stdout[-600:]}")
            if not os.path.exists(results_csv):
                raise RuntimeError(f"gosom non ha prodotto output. {proc.stderr[-500:]}")
            if os.path.getsize(results_csv) == 0:
                raise RuntimeError(f"gosom ha prodotto un CSV vuoto. stderr: {proc.stderr[-500:]}")

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
