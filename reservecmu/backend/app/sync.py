"""Fetch 25Live space list + availability, merge, and update the database.

  python -m app.sync                      # today, 1 day
  python -m app.sync --date 2026-11-20    # a specific date
  python -m app.sync --days 3             # today + next 2 days
  python -m app.sync --dump merged.json   # also write the merged snapshot (space + busy intervals)
"""
import argparse, asyncio, json, logging
from datetime import date as Date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import httpx
from . import config, db, ingest, live25

def merge(conn, day):
    """One record per space with its static info and busy intervals for `day` (what the DB join gives the API)."""
    busy = {}
    for r in conn.execute("SELECT * FROM busy WHERE date=? ORDER BY start_min", (day,)): busy.setdefault(r["space_id"], []).append({"start": r["start_min"], "end": r["end_min"], "kind": r["kind"]})
    return [{**dict(r), "date": day, "busy": busy.get(r["id"], [])} for r in conn.execute("SELECT * FROM spaces WHERE active=1 ORDER BY short_name")]

async def sync(conn, days: list[str], client=None):
    own = client is None; client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        list_pages = await live25.fetch_list(client)
        fetched = {d: await live25.fetch_availability(client, d) for d in days}      # fetch EVERYTHING before writing anything
    finally:
        if own: await client.aclose()
    report = {}
    for d, pages in fetched.items():
        v = live25.validate(list_pages, pages)
        if v["with_availability"] < 0.9 * v["spaces"]: raise live25.SyncError(f"{d}: availability covers only {v['with_availability']}/{v['spaces']} spaces; not updating")
        report[d] = v
    n_spaces = ingest.load_spaces(conn, list_pages)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n_busy = {d: ingest.load_availability(conn, pages, now) for d, pages in fetched.items()}
    return {"spaces": n_spaces, "busy_intervals": n_busy, "validation": report}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date"); ap.add_argument("--days", type=int, default=1); ap.add_argument("--dump")
    a = ap.parse_args(); logging.basicConfig(level=logging.INFO)
    start = Date.fromisoformat(a.date) if a.date else datetime.now(ZoneInfo(config.TIMEZONE)).date()
    days = [(start + timedelta(days=i)).isoformat() for i in range(a.days)]
    conn = db.connect(); db.init(conn)
    rep = asyncio.run(sync(conn, days))
    print(json.dumps({k: v for k, v in rep.items() if k != "validation"}), "| uncovered spaces per day:", {d: len(v["missing_availability"]) for d, v in rep["validation"].items()})
    if a.dump: json.dump({d: merge(conn, d) for d in days}, open(a.dump, "w"), indent=1); print("wrote", a.dump)
