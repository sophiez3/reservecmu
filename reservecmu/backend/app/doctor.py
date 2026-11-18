"""Health check. Run from the backend folder:  python -m app.doctor"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from . import config, db, search

def main() -> int:
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
    print("database file:", os.path.abspath(config.DB_PATH))
    print("today (Pittsburgh):", today, "| now:", datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%H:%M"))
    hr = datetime.now(ZoneInfo(config.TIMEZONE)).hour
    if hr >= 22 or hr < 7: print("NOTE: it is late. Free times only cover 07:00-23:00 and gaps under 30 min are hidden, so 'now' searches return nothing at night. Test with a daytime window such as 14:00-15:00.")
    print("OPENROUTER_API_KEY:", "set" if config.OPENROUTER_API_KEY else "NOT SET (check backend/.env, then restart the server)",
          "| model:", config.OPENROUTER_MODEL, "| max_tokens:", config.OPENROUTER_MAX_TOKENS)
    if not os.path.exists(config.DB_PATH):
        print("\nPROBLEM: no database yet.\nFIX: python -m app.ingest data/raw_pages.txt data/availability.txt --demo-today"); return 1
    c = db.connect()
    spaces = c.execute("SELECT COUNT(*) FROM spaces").fetchone()[0]
    dates = [r[0] for r in c.execute("SELECT date FROM availability_dates ORDER BY date")]
    print("spaces:", spaces, "| availability dates stored:", dates or "none")
    if not spaces:
        print("\nPROBLEM: database is empty.\nFIX: python -m app.ingest data/raw_pages.txt data/availability.txt --demo-today"); return 1
    if not any(d >= today for d in dates):
        print(f"\nPROBLEM: no availability for today ({today}) or later, so every search is empty.\n"
              "FIX (testing): python -m app.ingest data/raw_pages.txt data/availability.txt --demo-today\n"
              "FIX (real data): python -m app.sync\nThen restart the server."); return 1
    n = len(search.search(c, search.SearchParams(min_capacity=4, limit=200), today))
    print(f"spaces free somewhere today that seat 4+: {n}")
    if not n:
        print("\nPROBLEM: data is loaded but the search finds nothing. Please send this whole output."); return 1
    print("\nOK: the database looks good. If the assistant still says nothing matched, the server is probably running old code: stop it (Ctrl+C) and start it again.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
