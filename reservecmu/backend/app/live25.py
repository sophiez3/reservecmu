"""Fetch CMU's space list and availability from 25Live using the exact endpoints observed in DevTools."""
import asyncio
import httpx
from . import config

LIST_URL = ("https://25live.collegenet.com/25live/data/cmu/run/list/listdata.json?compsubject=location&sort=name&order=asc"
            "&page={page}&page_size=100&obj_cache_accl=0&category_id=76+81+209&caller=pro-ListService.getData")
# Page 1 is your URL verbatim; later pages append &page=N (ASSUMPTION: 25Live paginates availability with the same `page` param as list).
AVAIL_URL = ("https://25live.collegenet.com/25live/data/cmu/run/availability/availabilitydata.json?obj_cache_accl=0"
             "&start_dt={date}T00:00:00&comptype=availability&compsubject=location&page_size=100&spaces_category_id=76+81+209"
             "&include=closed+blackouts+pending+related+empty&caller=pro-AvailService.getData")
POLITE_DELAY = 0.5

class SyncError(RuntimeError): pass

def _headers():
    h = {"User-Agent": config.SCRAPER_UA, "Accept": "application/json"}
    if config.AVAILABILITY_COOKIE: h["Cookie"] = config.AVAILABILITY_COOKIE
    return h

async def _get_json(client, url):
    r = await client.get(url, headers=_headers()); r.raise_for_status()
    try: return r.json()
    except ValueError: raise SyncError("non-JSON response (login page / blocked / expired session?)")

async def fetch_list(client: httpx.AsyncClient) -> list[dict]:
    pages, page, total = [], 1, 1
    while page <= total:
        d = await _get_json(client, LIST_URL.format(page=page))
        if "rows" not in d: raise SyncError("listdata: unexpected payload shape")
        pages.append(d)
        if page == 1 and d["rows"]: total = int(d["rows"][0].get("pages", 1))   # first row carries pages/count
        page += 1; await asyncio.sleep(POLITE_DELAY)
    expected = int(pages[0]["rows"][0].get("count", 0)) if pages[0]["rows"] else 0
    got = sum(len(p["rows"]) for p in pages)
    if expected and got != expected: raise SyncError(f"listdata: expected {expected} spaces, got {got}")
    return pages

async def fetch_availability(client: httpx.AsyncClient, day: str) -> list[dict]:
    pages, page, total = [], 1, 1
    while page <= total:
        url = AVAIL_URL.format(date=day) + (f"&page={page}" if page > 1 else "")
        d = await _get_json(client, url)
        if "subjects" not in d: raise SyncError("availability: unexpected payload shape")
        pages.append(d); total = int(d.get("page_count", 1)); page += 1; await asyncio.sleep(POLITE_DELAY)
    ids = [s["itemId"] for p in pages for s in p["subjects"]]
    if len(ids) != len(set(ids)): raise SyncError("availability: duplicate spaces across pages (page parameter probably ignored)")
    if any(not s["item_date"].startswith(day) for p in pages for s in p["subjects"]): raise SyncError("availability: returned a different date than requested")
    return pages

def validate(list_pages, avail_pages):
    """Cross-check the two feeds before touching the database."""
    list_ids = {r["row"][0]["itemId"] for p in list_pages for r in p["rows"]}
    av_ids = {s["itemId"] for p in avail_pages for s in p["subjects"]}
    return {"spaces": len(list_ids), "with_availability": len(av_ids & list_ids), "missing_availability": sorted(list_ids - av_ids), "unknown_to_list": sorted(av_ids - list_ids)}
