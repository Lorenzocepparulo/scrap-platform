"""FastAPI app — API + dashboard statica."""
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, scheduler
from .scrapers import registry, gmaps

app = FastAPI(title="Scrap Solovera", docs_url=None, redoc_url=None)
registry.init_registry()

STATIC_DIR = Path(__file__).parent / "static"
DOWNLOADS_DIR = config.DATA_DIR / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ---------- Auth (semplice, password da env) ----------
def _check_auth(request: Request) -> bool:
    cookie = request.cookies.get("scrap_auth")
    import hashlib
    expected = hashlib.sha256(config.DASHBOARD_PASSWORD.encode()).hexdigest()
    return cookie == expected


def _require_auth(request: Request):
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Autenticazione richiesta")


@app.on_event("startup")
def _startup():
    db.init_db()
    scheduler.start()


@app.on_event("shutdown")
def _shutdown():
    scheduler.shutdown()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not _check_auth(request):
        return FileResponse(STATIC_DIR / "login.html")
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if body.get("password") == config.DASHBOARD_PASSWORD:
        import hashlib
        resp = JSONResponse({"ok": True})
        resp.set_cookie("scrap_auth", hashlib.sha256(config.DASHBOARD_PASSWORD.encode()).hexdigest(),
                        httponly=True, max_age=60 * 60 * 24 * 30, samesite="lax")
        return resp
    raise HTTPException(status_code=401, detail="Password errata")


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("scrap_auth")
    return resp


# ---------- Portali disponibili ----------
@app.get("/api/portali")
def portali(request: Request):
    _require_auth(request)
    return registry.list_portals()


# ---------- Monitor (Sezione A) ----------
@app.post("/api/monitors")
async def create_monitor(request: Request):
    _require_auth(request)
    body = await request.json()
    required = ["nome", "portale", "url_ricerca"]
    for k in required:
        if not body.get(k):
            raise HTTPException(status_code=400, detail=f"Manca il campo {k}")
    mid = db.create_monitor(
        nome=body["nome"], portale=body["portale"], url_ricerca=body["url_ricerca"],
        filtri=body.get("filtri"), frequenza_ore=body.get("frequenza_ore", config.DEFAULT_FREQUENCY_HOURS),
        campi=body.get("campi"), max_pages=int(body.get("max_pages") or 0),
    )
    scheduler.schedule_monitor(mid)
    db.add_log("info", f"Monitor '{body['nome']}' creato (id {mid})")
    return db.get_monitor(mid)


@app.get("/api/monitors")
def get_monitors(request: Request):
    _require_auth(request)
    out = []
    for m in db.list_monitors():
        m["job_stato"] = scheduler.job_status(m["id"])
        m["prossima_esecuzione"] = scheduler.next_run(m["id"])
        m["annunci_count"] = len(db.list_annunci(m["id"]))
        out.append(m)
    return out


@app.get("/api/monitors/{mid}")
def get_monitor_detail(mid: int, request: Request):
    _require_auth(request)
    m = db.get_monitor(mid)
    if not m:
        raise HTTPException(status_code=404, detail="Monitor non trovato")
    m["annunci"] = db.list_annunci(mid)
    m["ribassi"] = [dict(r) for r in _ribassi(mid)]
    return m


def _ribassi(monitor_id: int):
    with db.db() as conn:
        return conn.execute("SELECT * FROM ribassi WHERE monitor_id=? ORDER BY id DESC LIMIT 100", (monitor_id,)).fetchall()


@app.post("/api/monitors/{mid}/run")
def run_now(mid: int, request: Request):
    _require_auth(request)
    from .monitor import run_monitor
    t = threading.Thread(target=run_monitor, args=(mid,), daemon=True)
    t.start()
    return {"ok": True, "messaggio": "Esecuzione avviata in background"}


@app.post("/api/monitors/{mid}/pause")
def pause(mid: int, request: Request):
    _require_auth(request)
    db.update_monitor(mid, stato="pausa")
    scheduler.pause_monitor(mid)
    return {"ok": True}


@app.post("/api/monitors/{mid}/resume")
def resume(mid: int, request: Request):
    _require_auth(request)
    db.update_monitor(mid, stato="attivo")
    scheduler.resume_monitor(mid)
    return {"ok": True}


@app.delete("/api/monitors/{mid}")
def delete(mid: int, request: Request):
    _require_auth(request)
    scheduler.remove_monitor(mid)
    db.delete_monitor(mid)
    db.add_log("info", f"Monitor {mid} eliminato")
    return {"ok": True}


# ---------- Google Maps one-shot (Sezione B) ----------
@app.post("/api/maps")
async def launch_maps(request: Request):
    _require_auth(request)
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query mancante")
    campi = body.get("campi") or []
    target = int(body.get("target") or 0)
    keep_all = bool(body.get("all_fields"))
    job_id = db.create_maps_job(query, campi, target=target)
    t = threading.Thread(target=gmaps.run_maps_job, args=(job_id, query, campi, target), kwargs={"keep_all": keep_all}, daemon=True)
    t.start()
    db.add_log("info", f"Job Maps avviato: {query}")
    return db.get_maps_job(job_id)


@app.get("/api/maps/jobs")
def maps_jobs(request: Request):
    _require_auth(request)
    return db.list_maps_jobs()


@app.delete("/api/maps/jobs/{job_id}")
def delete_maps_job(job_id: int, request: Request):
    """Elimina una ricerca Maps e il relativo file Excel (per pulire la sezione)."""
    _require_auth(request)
    job = db.get_maps_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job non trovato")
    if job.get("filename"):
        path = DOWNLOADS_DIR / os.path.basename(job["filename"])
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
    with db.db() as conn:
        conn.execute("DELETE FROM jobs_maps WHERE id=?", (job_id,))
    db.add_log("info", f"Job Maps {job_id} eliminato ({job.get('query', '')[:60]})")
    return {"ok": True}


@app.get("/api/download/{filename}")
def download(filename: str, request: Request):
    _require_auth(request)
    safe = os.path.basename(filename)
    path = DOWNLOADS_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="File non trovato")
    return FileResponse(path, filename=safe, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------- Log ----------
@app.get("/api/logs")
def logs(request: Request, limit: int = 100):
    _require_auth(request)
    return db.list_log(limit=min(limit, 500))


# ---------- Debug (temporaneo, per verificare il fetch dei portali) ----------
@app.get("/api/debug/fetch")
def debug_fetch(url: str, request: Request):
    """Endpoint temporaneo: esegue fetch_html su un URL e mostra cosa restituisce."""
    _require_auth(request)
    from .anti_bot import fetch_html
    try:
        html = fetch_html(url)
        return {
            "len": len(html),
            "has_item_link": "item-link" in html,
            "has_inmueble": "inmueble" in html,
            "has_nd_list": "nd-list__item" in html,
            "has_it_card": "it-card" in html,
            "has_cmsg": "cmsg" in html,
            "has_datadome": "datadome" in html.lower(),
            "snippet": html[:300],
        }
    except Exception as e:
        return {"errore": str(e)}


# ---------- Health ----------
@app.get("/api/health")
def health():
    return {"ok": True, "time": datetime.now().isoformat()}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
