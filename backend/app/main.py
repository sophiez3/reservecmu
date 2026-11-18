import asyncio, logging, os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal, Optional
from zoneinfo import ZoneInfo
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from . import agent, config, db, ingest, refresh
from .search import AMENITIES, SearchParams, search

@asynccontextmanager
async def lifespan(_):
    if config.DEMO_MODE:   # sample data stamped as today so the app is usable without a live 25Live fetch
        c = db.connect(); db.init(c); today = now_local().date().isoformat()
        if not c.execute("SELECT 1 FROM availability_dates WHERE date>=?", (today,)).fetchone():
            ingest.ingest(os.path.join(config.DATA_DIR, "raw_pages.txt"), os.path.join(config.DATA_DIR, "availability.txt"), c, as_date=today)
            log.warning("DEMO MODE: loaded saved sample availability as %s. These free times are NOT real.", today)
        c.close()
    try:
        c = db.connect(); log.info("database %s: %d spaces, availability dates %s", config.DB_PATH, c.execute("SELECT COUNT(*) FROM spaces").fetchone()[0], [r[0] for r in c.execute("SELECT date FROM availability_dates")]); c.close()
    except Exception as e: log.warning("database not ready: %s (run python -m app.ingest ...)", e)
    task = asyncio.create_task(refresh.loop()) if config.REFRESH_ENABLED else None
    yield
    if task: task.cancel()

log = logging.getLogger("uvicorn.error")
app = FastAPI(title="ReserveCMU API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["*"])
Db = Annotated[object, Depends(db.get_db)]

def now_local(): return datetime.now(ZoneInfo(config.TIMEZONE))

@app.get("/health")
def health(): return {"ok": True}

@app.get("/meta")
def meta(conn: Db):
    q = lambda s: [r[0] for r in conn.execute(s)]
    return {"availability_dates": q("SELECT date FROM availability_dates"),
            "demo_mode": config.DEMO_MODE, "today": now_local().date().isoformat(),
            "has_current_data": bool(conn.execute("SELECT 1 FROM availability_dates WHERE date>=?", (now_local().date().isoformat(),)).fetchone()),
            "availability_as_of": {r[0]: r[1] for r in conn.execute("SELECT date, fetched_at FROM availability_dates")},
            "refresh": refresh.status | {"enabled": config.REFRESH_ENABLED, "interval_seconds": config.REFRESH_SECONDS}, "space_types": q("SELECT DISTINCT space_type FROM spaces ORDER BY 1"),
            "buildings": q("SELECT DISTINCT building FROM spaces ORDER BY 1"), "amenities": AMENITIES,
            "affiliations": q("SELECT DISTINCT restricted_to FROM spaces WHERE restricted_to IS NOT NULL"),
            "counts": {"spaces": q("SELECT COUNT(*) FROM spaces")[0], "with_availability": q("SELECT COUNT(*) FROM spaces WHERE has_availability=1")[0]}}

@app.get("/spaces")
def list_spaces(conn: Db, p: Annotated[SearchParams, Query()]):
    return search(conn, p, now_local().date().isoformat())

@app.get("/spaces/{space_id}")
def get_space(space_id: int, conn: Db):
    res = [s for s in search(conn, SearchParams(require_availability=False, limit=200, affiliation=None), now_local().date().isoformat()) if s["id"] == space_id]
    if not res:  # may be affiliate-restricted or unknown
        r = conn.execute("SELECT restricted_to FROM spaces WHERE id=?", (space_id,)).fetchone()
        if r is None: raise HTTPException(404, "space not found")
        raise HTTPException(403, f"restricted to {r['restricted_to']} affiliates")
    return res[0]

class AgentIn(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    affiliation: Optional[str] = None

@app.post("/agent/recommend")
async def recommend(body: AgentIn, conn: Db):
    n = now_local()
    dates = {r[0]: r[1] for r in conn.execute("SELECT date, fetched_at FROM availability_dates")}
    if not any(d >= n.date().isoformat() for d in dates):   # nothing for today or later: say so instead of "nothing matched"
        latest = f" (the newest data is for {max(dates)})" if dates else ""
        return {"summary": f"Availability for today hasn’t been loaded yet{latest}, so I can’t tell what’s free right now. Please check back soon, or look in 25Live directly.", "recommendations": []}
    try:
        return await agent.recommend(conn, body.query, n.date().isoformat(), n.strftime("%H:%M"), dates, body.affiliation)
    except RuntimeError as e: raise HTTPException(503, str(e))
    except agent.ProviderError as e:
        log.error("agent: %s", e); raise HTTPException(502, str(e))
    except Exception as e:
        log.exception("agent failed"); raise HTTPException(502, f"assistant error: {type(e).__name__}")

class FeedbackIn(BaseModel):
    space_id: int
    noise: Optional[int] = Field(None, ge=1, le=3)
    brightness: Optional[int] = Field(None, ge=1, le=3)
    temperature: Optional[int] = Field(None, ge=1, le=3)

@app.post("/feedback", status_code=201)
def feedback(body: FeedbackIn, conn: Db):
    if not conn.execute("SELECT 1 FROM spaces WHERE id=?", (body.space_id,)).fetchone(): raise HTTPException(404, "space not found")
    if body.noise is body.brightness is body.temperature is None: raise HTTPException(422, "provide at least one rating")
    conn.execute("INSERT INTO feedback(space_id,noise,brightness,temperature) VALUES(?,?,?,?)", (body.space_id, body.noise, body.brightness, body.temperature))
    conn.commit()
    return {"ok": True}
