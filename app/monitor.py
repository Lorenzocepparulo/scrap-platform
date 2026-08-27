"""Servizio monitor: esegue uno scrape della zona e aggiorna DB + Google Sheets.

Logica (il cuore della piattaforma):
- ID nuovo               -> "nuovo immobile", registra data_primo_avvistamento
- ID presente e online   -> aggiorna data_ultimo_avvistamento; se il prezzo è
                            cambiato registra un RIBASSO (data, vecchio -> nuovo,
                            contatore incrementato)
- ID che c'era ma manca  -> "non più online" (presunta vendita/ritiro),
                            giorni_online = ultimo - primo, data_scomparsa
"""
import traceback
from datetime import datetime

from . import db, sheets
from .scrapers import registry


def run_monitor(monitor_id: int) -> dict:
    monitor = db.get_monitor(monitor_id)
    if not monitor:
        return {"ok": False, "errore": "Monitor non trovato"}
    if monitor["stato"] != "attivo":
        return {"ok": False, "errore": "Monitor in pausa"}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    esito = {"ok": False, "errore": None, "nuovi": 0, "ribassi": 0, "scomparsi": 0, "totale": 0}
    try:
        scraper = registry.get_scraper(monitor["portale"])
        filtri = monitor.get("filtri") or {}
        import json
        if isinstance(filtri, str):
            filtri = json.loads(filtri) if filtri else {}
        annunci = scraper.scrape(monitor["url_ricerca"], filtri)

        # 1. aggiorna/inserisci annunci visti ora
        seen_ids = set()
        nuovi, ribassi = [], []
        for a in annunci:
            seen_ids.add(a.annuncio_id)
            res = db.upsert_annuncio(monitor_id, a.annuncio_id, a.url, a.to_dict(), a.prezzo)
            if res["esito"] == "nuovo":
                nuovi.append(a.to_dict())
            elif res["esito"] == "ribasso":
                ribassi.append({
                    "annuncio_id": a.annuncio_id,
                    "url": a.url,
                    "prezzo_vecchio": res["prezzo_vecchio"],
                    "prezzo_nuovo": res["prezzo_nuovo"],
                })

        # 2. annunci attivi non più visti -> venduto/rimosso
        scomparsi = []
        attivi = db.list_annunci(monitor_id, solo_attivi=True)
        for a in attivi:
            if a["annuncio_id"] not in seen_ids:
                res = db.mark_scomparso(monitor_id, a["annuncio_id"], now)
                if res:
                    res["url"] = a["url"]
                    scomparsi.append(res)

        riepilogo = (
            f"Totale {len(annunci)} annunci · {len(nuovi)} nuovi · "
            f"{len(ribassi)} ribassi · {len(scomparsi)} venduti/rimossi"
        )
        esito = {"ok": True, "nuovi": len(nuovi), "ribassi": len(ribassi),
                 "scomparsi": len(scomparsi), "totale": len(annunci),
                 "riepilogo": riepilogo}

        db.update_monitor(monitor_id, ultima_esecuzione=now, esito_ultima=riepilogo)
        db.add_log("info", f"Monitor '{monitor['nome']}': {riepilogo}", monitor_id)

        # 3. sync Google Sheets (se configurato)
        sheets.sync_monitor(monitor_id, nuovi, ribassi, scomparsi, riepilogo)

    except Exception as e:
        db.add_log("errore", f"Monitor '{monitor['nome']}': {e}", monitor_id)
        db.update_monitor(monitor_id, ultima_esecuzione=now, esito_ultima=f"ERRORE: {e}")
        esito = {"ok": False, "errore": str(e)}

    return esito
