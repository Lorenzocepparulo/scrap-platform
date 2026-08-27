"""Scheduler APScheduler: job ricorrenti per i monitor attivi + job one-shot Maps."""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import config, db

scheduler = BackgroundScheduler(timezone=config.SCHEDULER_TIMEZONE)

# Job one-shot Maps in esecuzione (thread semplice, niente scheduler)
_maps_threads: dict[int, object] = {}


def _job_wrapper(monitor_id: int):
    from .monitor import run_monitor
    run_monitor(monitor_id)


def schedule_monitor(monitor_id: int):
    monitor = db.get_monitor(monitor_id)
    if not monitor or monitor["stato"] != "attivo":
        return
    job_id = f"monitor-{monitor_id}"
    try:
        scheduler.add_job(
            _job_wrapper,
            trigger=IntervalTrigger(hours=float(monitor["frequenza_ore"])),
            args=[monitor_id],
            id=job_id,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
    except Exception as e:
        db.add_log("errore", f"Impossibile schedulare monitor {monitor_id}: {e}", monitor_id)


def pause_monitor(monitor_id: int):
    job_id = f"monitor-{monitor_id}"
    try:
        scheduler.pause_job(job_id)
    except Exception:
        pass


def resume_monitor(monitor_id: int):
    job_id = f"monitor-{monitor_id}"
    try:
        scheduler.resume_job(job_id)
    except Exception:
        schedule_monitor(monitor_id)


def remove_monitor(monitor_id: int):
    job_id = f"monitor-{monitor_id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass


def start():
    if scheduler.running:
        return
    scheduler.start()
    for m in db.list_monitors():
        if m["stato"] == "attivo":
            schedule_monitor(m["id"])


def shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def job_status(monitor_id: int) -> str:
    job_id = f"monitor-{monitor_id}"
    try:
        job = scheduler.get_job(job_id)
        if job is None:
            return "non_schedulato"
        return "pausa" if job.next_run_time is None else "attivo"
    except Exception:
        return "non_schedulato"


def next_run(monitor_id: int) -> str | None:
    job_id = f"monitor-{monitor_id}"
    try:
        job = scheduler.get_job(job_id)
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return None
