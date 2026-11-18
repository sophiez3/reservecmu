"""Background loop: re-runs the same sync as the CLI every REFRESH_SECONDS. A failed run keeps serving the previous data."""
import asyncio, logging, random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import httpx
from . import config, db, sync

log = logging.getLogger("refresh")
status = {"last_success": None, "last_error": None, "consecutive_failures": 0}

async def refresh_once(client=None, today=None) -> int:
    today = today or datetime.now(ZoneInfo(config.TIMEZONE)).date()
    days = [(today + timedelta(days=i)).isoformat() for i in range(config.REFRESH_DAYS)]
    try:
        conn = db.connect()
        try: rep = await sync.sync(conn, days, client)
        finally: conn.close()
        status.update(last_success=datetime.now(timezone.utc).isoformat(timespec="seconds"), last_error=None, consecutive_failures=0)
        return sum(rep["busy_intervals"].values())
    except Exception as e:  # keep serving previous data
        status["consecutive_failures"] += 1; status["last_error"] = f"{type(e).__name__}: {e}"
        log.warning("25Live refresh failed (%d in a row): %s", status["consecutive_failures"], e)
        return 0

async def loop():
    while True:
        await refresh_once()
        backoff = min(status["consecutive_failures"], 5) * config.REFRESH_SECONDS * 0.5
        await asyncio.sleep(config.REFRESH_SECONDS + backoff + random.uniform(0, 15))
