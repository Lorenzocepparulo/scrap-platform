"""Sync su Google Sheets via Maton API (connessione OAuth google-sheets).

Il primo run crea lo spreadsheet (fogli: Database, Nuovi, Ribassi,
Venduti-Rimossi, Log) e salva sheet_id/sheet_url sul monitor.
I run successivi aggiornano lo stesso file.
Usa la MATON_API_KEY (stessa di Gmail/Drive): niente service account.
"""
from datetime import datetime
from urllib.parse import quote

import requests

from . import config
from . import db

BASE = "https://api.maton.ai"
FOGLI = ["Database", "Nuovi", "Ribassi", "Venduti-Rimossi", "Log"]


def _headers():
    if not config.MATON_API_KEY:
        raise RuntimeError("MATON_API_KEY non configurata")
    return {"Authorization": f"Bearer {config.MATON_API_KEY}", "Content-Type": "application/json"}


def _create_spreadsheet(title: str) -> tuple[str, str]:
    """Crea lo spreadsheet via API Sheets (il token Maton è owner del file)."""
    r = requests.post(
        f"{BASE}/google-sheets/v4/spreadsheets",
        headers=_headers(),
        json={"properties": {"title": title}},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    sid = data["spreadsheetId"]
    url = data.get("spreadsheetUrl") or f"https://docs.google.com/spreadsheets/d/{sid}"
    return sid, url


def _batch_update(sid: str, requests_list: list):
    r = requests.post(
        f"{BASE}/google-sheets/v4/spreadsheets/{sid}:batchUpdate",
        headers=_headers(),
        json={"requests": requests_list},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _rename_default_sheet(sid: str, new_title: str):
    """Rinomina il foglio di default (Foglio1) nel titolo voluto."""
    try:
        meta = requests.get(f"{BASE}/google-sheets/v4/spreadsheets/{sid}", headers=_headers(), timeout=30).json()
        sheet_id = meta["sheets"][0]["properties"]["sheetId"]
        _batch_update(sid, [{
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "title": new_title},
                "fields": "title",
            }
        }])
    except Exception:
        pass  # se fallisce il rename, i fogli vengono creati comunque


def _ensure_sheet(monitor: dict):
    """Recupera lo spreadsheet esistente o lo crea al primo run."""
    if monitor.get("sheet_id"):
        return monitor["sheet_id"], monitor.get("sheet_url") or ""
    sid, url = _create_spreadsheet(f"Scrap Portali — {monitor['nome']} ({datetime.now().strftime('%Y-%m-%d')})")
    # rinomina Foglio1 in Database e crea gli altri fogli
    _rename_default_sheet(sid, "Database")
    to_add = [f for f in FOGLI if f != "Database"]
    _batch_update(sid, [{"addSheet": {"properties": {"title": f}}} for f in to_add])
    # condivisione con l'email configurata (opzionale)
    if config.SHEET_SHARE_EMAIL:
        try:
            requests.post(
                f"{BASE}/google-drive/drive/v3/files/{sid}/permissions",
                headers=_headers(),
                json={"role": "writer", "type": "user", "emailAddress": config.SHEET_SHARE_EMAIL},
                timeout=30,
            )
        except Exception:
            pass
    db.update_monitor(monitor["id"], sheet_id=sid, sheet_url=url)
    return sid, url


def _update_values(sid: str, sheet: str, values: list):
    """Scrive (sostituendo) i valori in un foglio, a partire da A1."""
    if not values:
        return
    rng = f"{sheet}!A1"
    r = requests.put(
        f"{BASE}/google-sheets/v4/spreadsheets/{sid}/values/{quote(rng)}?valueInputOption=USER_ENTERED",
        headers=_headers(),
        json={"range": rng, "majorDimension": "ROWS", "values": values},
        timeout=30,
    )
    r.raise_for_status()


def _clear_sheet(sid: str, sheet: str):
    """Svuota un foglio."""
    rng = f"{sheet}!A1:Z1000"
    try:
        requests.post(
            f"{BASE}/google-sheets/v4/spreadsheets/{sid}/values/{quote(rng)}:clear",
            headers=_headers(),
            json={},
            timeout=30,
        )
    except Exception:
        pass


def _append_values(sid: str, sheet: str, values: list):
    """Accoda righe in fondo a un foglio (per accumuli storici)."""
    if not values:
        return
    rng = f"{sheet}!A1"
    r = requests.post(
        f"{BASE}/google-sheets/v4/spreadsheets/{sid}/values/{quote(rng)}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
        headers=_headers(),
        json={"range": rng, "majorDimension": "ROWS", "values": values},
        timeout=30,
    )
    r.raise_for_status()


_COLS_DB = [
    "annuncio_id", "url", "prezzo_attuale", "prezzo_iniziale", "num_ribassi",
    "tipologia", "superficie_mq", "locali", "bagni", "piano", "indirizzo",
    "contratto", "privato_agenzia", "nome_agenzia", "riferimento",
    "data_primo_avvistamento", "data_ultimo_avvistamento", "giorni_online", "stato",
]
_HEADERS_DB = [
    "ID annuncio", "URL", "Prezzo attuale", "Prezzo iniziale", "Num. ribassi",
    "Tipologia", "Superficie mq", "Locali", "Bagni", "Piano", "Indirizzo/Zona",
    "Contratto", "Privato/Agenzia", "Nome agenzia", "Riferimento",
    "Data primo avvistamento", "Data ultimo avvistamento", "Giorni online", "Stato",
]


def _row_annuncio(a: dict) -> list:
    return [a.get(c) or "" for c in _COLS_DB]


def sync_monitor(monitor_id: int, nuovi: list, ribassi: list, scomparsi: list, riepilogo: str):
    """Aggiorna i fogli dopo un run. Se manca MATON_API_KEY, logga e prosegue."""
    monitor = db.get_monitor(monitor_id)
    if not monitor:
        return
    try:
        sid, url = _ensure_sheet(monitor)
    except Exception as e:
        db.add_log("errore", f"Sheets non sincronizzato (monitor {monitor_id}): {e}", monitor_id)
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        # Database (riga per annuncio, sostituzione completa)
        annunci = db.list_annunci(monitor_id)
        _clear_sheet(sid, "Database")
        rows = [_HEADERS_DB] + [_row_annuncio(a) for a in annunci]
        _update_values(sid, "Database", rows)

        # Nuovi (solo ultimo run)
        _clear_sheet(sid, "Nuovi")
        rows_nuovi = [["ID", "URL", "Prezzo", "Tipologia", "Zona", "Rilevato il"]]
        for n in nuovi:
            rows_nuovi.append([n.get("annuncio_id"), n.get("url"), n.get("prezzo"), n.get("tipologia"), n.get("indirizzo"), now])
        if len(rows_nuovi) > 1:
            _update_values(sid, "Nuovi", rows_nuovi)

        # Ribassi (accumulo storico con header la prima volta)
        if ribassi:
            rows_rib = []
            for r in ribassi:
                rows_rib.append([r["annuncio_id"], r.get("url"), now, r["prezzo_vecchio"], r["prezzo_nuovo"],
                                 round((r["prezzo_vecchio"] - r["prezzo_nuovo"]) / r["prezzo_vecchio"] * 100, 1)])
            _append_values(sid, "Ribassi", rows_rib)
            try:
                r = requests.get(f"{BASE}/google-sheets/v4/spreadsheets/{sid}/values/Ribassi!A1:F1", headers=_headers(), timeout=30)
                if not r.json().get("values"):
                    _update_values(sid, "Ribassi", [["ID", "URL", "Data", "Prezzo vecchio", "Prezzo nuovo", "%"]])
            except Exception:
                pass

        # Venduti-Rimossi (accumulo storico)
        if scomparsi:
            rows_v = []
            for s in scomparsi:
                rows_v.append([s["annuncio_id"], s.get("url"), now, s.get("giorni_online")])
            _append_values(sid, "Venduti-Rimossi", rows_v)
            try:
                r = requests.get(f"{BASE}/google-sheets/v4/spreadsheets/{sid}/values/Venduti-Rimossi!A1:D1", headers=_headers(), timeout=30)
                if not r.json().get("values"):
                    _update_values(sid, "Venduti-Rimossi", [["ID", "URL", "Data scomparsa", "Giorni online"]])
            except Exception:
                pass

        # Log esecuzioni (accumulo)
        _append_values(sid, "Log", [[now, riepilogo]])
        try:
            r = requests.get(f"{BASE}/google-sheets/v4/spreadsheets/{sid}/values/Log!A1:B1", headers=_headers(), timeout=30)
            if not r.json().get("values"):
                _update_values(sid, "Log", [["Timestamp", "Riepilogo"]])
        except Exception:
            pass

        db.add_log("info", f"Sheets aggiornato: {url}", monitor_id)
    except Exception as e:
        db.add_log("errore", f"Errore sync Sheets (monitor {monitor_id}): {e}", monitor_id)
