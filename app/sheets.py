"""Sync su Google Sheets via gspread + service account.

Il primo run crea lo spreadsheet (fogli: Database, Nuovi, Ribassi,
Venduti-Rimossi, Log) e salva sheet_id/sheet_url sul monitor.
I run successivi aggiornano lo stesso file.
"""
from datetime import datetime

from . import config
from . import db

FOGLI = ["Database", "Nuovi", "Ribassi", "Venduti-Rimossi", "Log"]


def _client():
    if not config.GOOGLE_SERVICE_ACCOUNT:
        raise RuntimeError("Service account Google non configurato (GOOGLE_SERVICE_ACCOUNT_JSON)")
    import gspread
    return gspread.service_account_from_dict(config.GOOGLE_SERVICE_ACCOUNT)


def _ensure_sheet(monitor: dict):
    """Recupera lo spreadsheet esistente o lo crea al primo run."""
    gc = _client()
    if monitor.get("sheet_id"):
        return gc.open_by_key(monitor["sheet_id"])
    sh = gc.create(f"Scrap Portali — {monitor['nome']} ({datetime.now().strftime('%Y-%m-%d')})")
    for f in FOGLI:
        try:
            sh.add_worksheet(title=f, rows=1000, cols=20)
        except Exception:
            pass
    if config.SHEET_SHARE_EMAIL:
        try:
            sh.share(config.SHEET_SHARE_EMAIL, perm_type="user", role="writer")
        except Exception:
            pass
    db.update_monitor(monitor["id"], sheet_id=sh.id, sheet_url=sh.url)
    return sh


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
    """Aggiorna i fogli dopo un run. Se manca il service account, logga e prosegue."""
    monitor = db.get_monitor(monitor_id)
    if not monitor:
        return
    try:
        sh = _ensure_sheet(monitor)
    except Exception as e:
        db.add_log("errore", f"Sheets non sincronizzato (monitor {monitor_id}): {e}", monitor_id)
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ws_db = sh.worksheet("Database")
        annunci = db.list_annunci(monitor_id)
        if annunci:
            rows = [_HEADERS_DB] + [_row_annuncio(a) for a in annunci]
        else:
            rows = [_HEADERS_DB]
        ws_db.clear()
        ws_db.update("A1", rows, value_input_option="USER_ENTERED")
        ws_db.format("A1:S1", {"textFormat": {"bold": True}})

        # Nuovi (solo ultimo run)
        ws_nuovi = sh.worksheet("Nuovi")
        rows_nuovi = [["ID", "URL", "Prezzo", "Tipologia", "Zona", "Rilevato il"]]
        for n in nuovi:
            rows_nuovi.append([n.get("annuncio_id"), n.get("url"), n.get("prezzo"), n.get("tipologia"), n.get("indirizzo"), now])
        ws_nuovi.clear()
        if len(rows_nuovi) > 1:
            ws_nuovi.update("A1", rows_nuovi, value_input_option="USER_ENTERED")

        # Ribassi (accumulo storico)
        ws_rib = sh.worksheet("Ribassi")
        if ribassi:
            rows_rib = [["ID", "URL", "Data", "Prezzo vecchio", "Prezzo nuovo", "%"]]
            for r in ribassi:
                rows_rib.append([r["annuncio_id"], r.get("url"), now, r["prezzo_vecchio"], r["prezzo_nuovo"],
                                 round((r["prezzo_vecchio"] - r["prezzo_nuovo"]) / r["prezzo_vecchio"] * 100, 1)])
            ws_rib.append_rows(rows_rib[1:], value_input_option="USER_ENTERED")
            if ws_rib.row_count == 1:
                ws_rib.update("A1", rows_rib[0])

        # Venduti-Rimossi (accumulo storico)
        ws_vend = sh.worksheet("Venduti-Rimossi")
        if scomparsi:
            rows_v = [["ID", "URL", "Data scomparsa", "Giorni online"]]
            for s in scomparsi:
                rows_v.append([s["annuncio_id"], s.get("url"), now, s.get("giorni_online")])
            ws_vend.append_rows(rows_v[1:], value_input_option="USER_ENTERED")
            if ws_vend.row_count == 1:
                ws_vend.update("A1", rows_v[0])

        # Log esecuzioni
        ws_log = sh.worksheet("Log")
        ws_log.append_row([now, riepilogo], value_input_option="USER_ENTERED")
        if ws_log.row_count == 1:
            ws_log.update("A1", [["Timestamp", "Riepilogo"]])

        db.add_log("info", f"Sheets aggiornato: {sh.url}", monitor_id)
    except Exception as e:
        db.add_log("errore", f"Errore sync Sheets (monitor {monitor_id}): {e}", monitor_id)
