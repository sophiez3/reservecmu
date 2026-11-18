"""One search function shared by the REST catalog AND the AI agent's tool, so both see identical data."""
import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator
from . import config

DAY_OPEN, DAY_CLOSE = 7 * 60, 23 * 60
NOISE = {1: "silent", 2: "quiet", 3: "lively"}
LIGHT = {1: "dim", 2: "moderate", 3: "bright"}
TEMP = {1: "cool", 2: "comfortable", 3: "warm"}
AMENITIES = ["whiteboard", "chalkboard", "tv_display", "projector", "computers", "video_conferencing",
             "document_camera", "speakers", "power_outlets", "accessible", "movable_furniture", "flip_chart", "mirrored_wall"]  # no printer data in 25Live

class SearchParams(BaseModel):
    date: Optional[str] = Field(None, description="YYYY-MM-DD; default today")
    start_time: Optional[str] = Field(None, description="HH:MM 24h; space must be free from here")
    end_time: Optional[str] = Field(None, description="HH:MM 24h; space must be free until here")
    min_capacity: Optional[int] = Field(None, ge=1, le=2000)
    space_types: Optional[list[str]] = None
    building: Optional[str] = Field(None, description="substring match")
    amenities: Optional[list[str]] = Field(None, description="all required; values from the amenity list")
    table_type: Optional[Literal["collaborative", "individual"]] = None
    noise_max: Optional[int] = Field(None, ge=1, le=3, description="1 silent, 2 quiet, 3 lively")
    brightness_min: Optional[int] = Field(None, ge=1, le=3)
    brightness_max: Optional[int] = Field(None, ge=1, le=3)
    temperature: Optional[Literal["cool", "comfortable", "warm"]] = None
    affiliation: Optional[str] = Field(None, description="user's school, e.g. Heinz; unlocks affiliate-only rooms")
    require_availability: bool = Field(True, description="drop spaces whose availability is unknown")
    limit: int = Field(8, ge=1, le=200)

    @field_validator("start_time", "end_time")
    @classmethod
    def _hhmm(cls, v):
        if v is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v): raise ValueError("use HH:MM")
        return v

def mins(t): return int(t[:2]) * 60 + int(t[3:])
def fmt(m): return f"{m // 60:02d}:{m % 60:02d}"

def merge(iv):
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]: out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out

def free_windows(busy, lo=DAY_OPEN, hi=DAY_CLOSE, min_len=30):
    cur, res = lo, []
    for a, b in merge(busy):
        if a - cur >= min_len and cur < hi: res.append([cur, min(a, hi)])
        cur = max(cur, b)
    if hi - cur >= min_len: res.append([cur, hi])
    return res

def search(conn, p: SearchParams, today: str):
    date = p.date or today
    covered = conn.execute("SELECT 1 FROM availability_dates WHERE date=?", (date,)).fetchone() is not None
    rows = conn.execute("SELECT * FROM spaces WHERE active=1").fetchall()
    amen, busy, fb = {}, {}, {}
    for r in conn.execute("SELECT * FROM space_amenities"): amen.setdefault(r["space_id"], set()).add(r["amenity"])
    for r in conn.execute("SELECT * FROM busy WHERE date=?", (date,)): busy.setdefault(r["space_id"], []).append((r["start_min"], r["end_min"]))
    for r in conn.execute("SELECT space_id, COUNT(*) n, AVG(noise) noise, AVG(brightness) brightness, AVG(temperature) temperature FROM feedback GROUP BY space_id"): fb[r["space_id"]] = r
    out = []
    for r in rows:
        sid = r["id"]
        if r["restricted_to"] and (p.affiliation or "").lower() != r["restricted_to"].lower(): continue
        if p.min_capacity and (r["max_capacity"] or 0) < p.min_capacity: continue
        if p.space_types and r["space_type"].lower() not in [t.lower() for t in p.space_types]: continue
        if p.building and p.building.lower() not in (r["building"] or "").lower(): continue
        have = amen.get(sid, set())
        if p.amenities and not set(p.amenities) <= have: continue
        if p.table_type and r["table_type"] not in (p.table_type, "flexible", None): continue
        known = bool(r["has_availability"] and covered)
        if p.require_availability and not known: continue
        wins = free_windows(busy.get(sid, [])) if known else None
        fits = None
        if known and p.start_time and p.end_time:
            fits = any(a <= mins(p.start_time) and mins(p.end_time) <= b for a, b in wins)
            if not fits: continue
        # crowd-sourced attributes: "verified" only once enough reports exist
        f, attrs, unverified, hits = fb.get(sid), {}, [], 0
        for key, labels, test in (("noise", NOISE, lambda v: p.noise_max is None or v <= p.noise_max),
                                  ("brightness", LIGHT, lambda v: (p.brightness_min is None or v >= p.brightness_min) and (p.brightness_max is None or v <= p.brightness_max)),
                                  ("temperature", TEMP, lambda v: p.temperature is None or labels[v] == p.temperature)):
            wanted = {"noise": p.noise_max, "brightness": p.brightness_min or p.brightness_max, "temperature": p.temperature}[key]
            if f and f["n"] >= config.MIN_REPORTS:
                v = round(f[key]); attrs[key] = {"label": labels[v], "reports": f["n"]}
                if not test(v): break
                hits += wanted is not None
            else:
                attrs[key] = None
                if wanted is not None: unverified.append(key)
        else:
            over = (r["max_capacity"] or 0) - (p.min_capacity or 0)
            out.append(dict(
                id=sid, name=r["short_name"], formal_name=r["formal_name"], building=r["building"], space_type=r["space_type"],
                max_capacity=r["max_capacity"], amenities=sorted(have),
                table_type={"value": r["table_type"], "source": r["table_type_source"]}, attributes=attrs,
                unverified_preferences=unverified, restricted_to=r["restricted_to"],
                availability={"known": known, "date": date, "free_windows": [f"{fmt(a)}-{fmt(b)}" for a, b in wins] if known else None},
                url=config.LIVE25_SPACE_URL.format(id=sid), _k=(-hits, over if p.min_capacity else 0, r["short_name"])))
    out.sort(key=lambda x: x["_k"])
    for x in out: x.pop("_k")
    return out[: p.limit]
