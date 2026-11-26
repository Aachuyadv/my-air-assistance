"""Hourly background collector for hosts without cron (e.g. Render web service).

Disable with AQ_SCHEDULER=0 (then use cron / Windows Task Scheduler to run
``python -m airguide.aq.collector`` every hour).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

from . import collector, db

log = logging.getLogger("airguide.aq")
_started = False


def _last_ok_age_s() -> float | None:
    with db.connect() as conn:
        row = conn.execute("SELECT created_at FROM api_logs WHERE status='ok' AND source!='' ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    t = datetime.strptime(row["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds()


def _loop() -> None:
    time.sleep(5)
    while True:
        try:
            age = _last_ok_age_s()
            if age is None or age > 50 * 60:
                log.info("AQ collection: %s", collector.collect())
        except Exception:  # never let the thread die
            log.exception("AQ collection crashed")
        now = time.time()
        time.sleep(3600 - (now % 3600) + 120)  # next hour + 2 min


def start() -> None:
    global _started
    if _started or os.environ.get("AQ_SCHEDULER", "1") == "0":
        return
    _started = True
    threading.Thread(target=_loop, name="aq-collector", daemon=True).start()
