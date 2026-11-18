"""Load scraped 25Live exports into SQLite.  Usage: python -m app.ingest raw_pages.txt availability.txt"""
import json, re, sys
from collections import Counter
from . import db

FEATURE_MAP = {
    "Board: Standard Whiteboard": "whiteboard", "Board: Sliding Chalkboard": "chalkboard",
    "Board: Standard Chalkboard": "chalkboard", "Flat Screen Display": "tv_display", "Screen: Lcd": "tv_display",
    "Media: Single Projection": "projector", "Media: Dual Projection": "projector",
    "Media: Video Conferencing": "video_conferencing", "Media: Document Camera": "document_camera",
    "Speakers: Installed": "speakers", "Seats: Powered": "power_outlets",
    "Wheelchair Accessible For Student": "accessible", "Wheelchair Accessible For Instructor": "accessible",
    "Furniture: Moveable Student Desks": "movable_furniture", "Mirrored Wall": "mirrored_wall", "Flip Chart": "flip_chart",
}
COLLAB = {"Conference", "Collaborative", "Banquet", "Hollow Square", "U Shape", "Cafe"}
INDIV = {"Classroom", "Table Rows", "Rows Of Chairs", "Auditorium"}

def read_stream(path):
    s = open(path, encoding="utf-8").read(); dec = json.JSONDecoder(); i = 0; out = []
    while True:
        while i < len(s) and s[i].isspace(): i += 1
        if i >= len(s): return out
        obj, i = dec.raw_decode(s, i); out.append(obj)

def split(v): return [x.strip() for x in re.sub(r"\s+", " ", v or "").split(",") if x.strip()]

def building_of(short, formal):
    f = re.sub(r"^Computer Lab - ", "", formal)
    left = f.split(" - ")[0]
    if " - " in f and not re.search(r"\d", left): return left.strip()   # "Cohon University Center - Caffee"
    m = re.match(r"^(.+?) [A-Z]?\d", f)                                  # "Baker Hall 140A Classroom"
    return m.group(1).strip() if m else None

def building_map(rows):
    """25Live's Building column is empty in the scrape, so infer it from formal names; short-name prefix is the fallback."""
    votes = {}
    for r in rows:
        b = building_of(r[1]["itemName"], r[2])
        if b and " " in r[1]["itemName"]: votes.setdefault(r[1]["itemName"].split()[0], Counter())[b] += 1
    return {k: v.most_common(1)[0][0] for k, v in votes.items()}

def space_type(cats, layouts, formal):
    if re.search(r"\bGym\b", formal): return "Fitness / Gym"
    if "Auditorium" in formal: return "Auditorium"
    if "Computer Lab - All" in cats: return "Computer Lab"
    if "Registrar Learning Halls" in cats: return "Lecture Hall"
    if "Registrar Classrooms" in cats: return "Classroom"
    if {"Cafe", "Outdoor Layout", "Atrium"} & set(layouts) or "Reception (Unchangeable Set-up)" in layouts: return "Social / Event Space"
    if "Conference" in layouts: return "Conference Room"
    if "Studio" in layouts: return "Studio"
    return "Other"

def load_spaces(conn, list_pages):
    """Upsert spaces from 25Live listdata pages. Feedback is preserved; spaces missing from the fetch are marked inactive."""
    db.init(conn)
    rows = [r["row"] for p in list_pages for r in p["rows"]]
    if not rows: raise ValueError("no spaces in listdata; refusing to update")
    bmap = building_map(rows)
    with conn:
        for r in rows:
            sid, short, formal = r[0]["itemId"], r[1]["itemName"].strip(), r[2]
            cats, feats, layouts = split(r[3]), split(r[4]), split(r[5])
            m = re.search(r"\(For (.+?) Affiliates\)|\((.+?) Affiliates Only\)", " ".join([formal] + cats))
            restricted = (m.group(1) or m.group(2)).strip() if m else None
            ls = set(layouts); c, i = bool(ls & COLLAB), bool(ls & INDIV)
            tt = "flexible" if c and i else "collaborative" if c else "individual" if i else None
            bld = building_of(short, formal) or bmap.get(short.split()[0].split("-")[0], formal.split(" (")[0])
            conn.execute("""INSERT INTO spaces(id,short_name,formal_name,building,space_type,max_capacity,default_capacity,layouts,categories,table_type,table_type_source,restricted_to,active)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET short_name=excluded.short_name, formal_name=excluded.formal_name,
                building=excluded.building, space_type=excluded.space_type, max_capacity=excluded.max_capacity, default_capacity=excluded.default_capacity,
                layouts=excluded.layouts, categories=excluded.categories, table_type=excluded.table_type, table_type_source=excluded.table_type_source,
                restricted_to=excluded.restricted_to, active=1""",
                (sid, short, formal, bld, space_type(cats, layouts, formal), r[6], r[7], ", ".join(layouts), ", ".join(cats),
                 tt, "derived_from_25live_layouts" if tt else None, restricted))
            amen = {FEATURE_MAP[f] for f in feats if f in FEATURE_MAP}
            if "Computer Lab - All" in cats: amen.add("computers")
            conn.execute("DELETE FROM space_features WHERE space_id=?", (sid,)); conn.execute("DELETE FROM space_amenities WHERE space_id=?", (sid,))
            conn.executemany("INSERT OR IGNORE INTO space_features VALUES(?,?)", [(sid, f) for f in feats])
            conn.executemany("INSERT OR IGNORE INTO space_amenities VALUES(?,?)", [(sid, a) for a in amen])
        ids = [r[0]["itemId"] for r in rows]
        conn.execute(f"UPDATE spaces SET active=0 WHERE id NOT IN ({','.join('?' * len(ids))})", ids)
    return len(rows)

def ingest(raw_path, avail_path, conn, as_date=None):
    """Offline/dev path: load the saved scrape files."""
    n = load_spaces(conn, read_stream(raw_path))
    n_events = load_availability(conn, read_stream(avail_path), as_date=as_date)
    return n, conn.execute("SELECT COUNT(*) FROM spaces WHERE has_availability=1").fetchone()[0], n_events

def load_availability(conn, pages, fetched_at=None, as_date=None):
    """Atomically replace busy intervals for every date present in `pages` (list of 25Live availability payloads).
    All-or-nothing: if anything is malformed the transaction rolls back and the previous data stays live."""
    known = {x[0] for x in conn.execute("SELECT id FROM spaces")}
    rows, dates, seen = [], {}, set()
    for av in pages:
        for s in av["subjects"]:
            if s["itemId"] not in known: continue
            date = as_date or s["item_date"][:10]; dates[date] = fetched_at or av.get("lastupdate"); seen.add((s["itemId"], date))
            for e in s.get("items", []):
                # NB: event titles are deliberately NOT stored (privacy); only the busy interval + kind.
                a, b = float(e["start"]), min(float(e["end"]), 24.0)
                kind = {1: "event", 2: "restriction", 3: "closed", 5: "private"}.get(e.get("type_id"), "other")
                rows.append((s["itemId"], date, round(a * 60), min(round(b * 60), 1440), kind))
    if not dates: raise ValueError("no usable availability rows; refusing to wipe existing data")
    with conn:  # single transaction
        for d, ts in dates.items():
            conn.execute("DELETE FROM busy WHERE date=?", (d,))
            conn.execute("INSERT OR REPLACE INTO availability_dates VALUES(?,?)", (d, ts))
        conn.executemany("INSERT INTO busy VALUES(?,?,?,?,?)", rows)
        conn.executemany("UPDATE spaces SET has_availability=1 WHERE id=?", [(i,) for i in {i for i, _ in seen}])
    return len(rows)

if __name__ == "__main__":
    # --demo-today relabels the saved file's date as today so you can try the app without a live 25Live fetch.
    # The busy times are NOT real for today; never use this for anything but local testing.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from . import config
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    demo = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat() if "--demo-today" in sys.argv else None
    c = db.connect(); print("spaces=%d with_availability=%d busy_intervals=%d" % ingest(args[0], args[1], c, as_date=demo))
    if demo: print("WARNING: demo mode. Availability from the saved file is now labeled as", demo, "and is not real.")
