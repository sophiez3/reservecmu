"""AI recommender. The LLM never sees raw tables: it picks search filters via a tool, the server runs them against SQLite,
and every recommended space id is validated against what the tool actually returned (no invented rooms)."""
import json, re
import httpx
from pydantic import ValidationError
from . import config
from .search import SearchParams, search, AMENITIES

class ProviderError(Exception):
    """OpenRouter rejected or failed the request; message is safe to show (never contains the key)."""

def _provider_msg(r):
    try: return str(r.json()["error"]["message"])[:200]
    except Exception: return (r.text or r.reason_phrase)[:200]

TOOL = {"type": "function", "function": {
    "name": "search_spaces",
    "description": "Search CMU reservable spaces with live availability. Omit any filter the user did not ask for. Times are 24h HH:MM.",
    "parameters": {**SearchParams.model_json_schema(), "properties": {k: v for k, v in SearchParams.model_json_schema()["properties"].items() if k != "require_availability"}}}}

def system_prompt(today, now, dates):
    return f"""You are ReserveCMU's space recommender. Today is {today}, current time {now} (Pittsburgh).
Availability data exists only for these dates (with last-refreshed time): {', '.join(f'{d} (as of {t})' for d, t in dates.items()) or 'none'}. If the data is more than ~30 minutes old relative to now, say it may be slightly out of date. Spaces can be booked by others at any moment, so never promise a room is free; the user confirms in 25Live. Amenity values: {', '.join(AMENITIES)}.
Rules:
- Call search_spaces to find candidates; never name a space that a tool call did not return. Refine and call again if results are empty or too broad.
- Only filter on what the user stated. Map "quiet" to noise_max=2, "silent" to 1; "bright" to brightness_min=3.
- Noise, brightness and temperature are crowd-sourced and often missing. If a result lists a requested preference under unverified_preferences, say it is unconfirmed. Never claim a noise/brightness/temperature value that is null.
- table_type with source "derived_from_25live_layouts" is a guess; say so briefly if it matters.
- Printers are not tracked. If asked, say you cannot confirm printers.
- If the date has no availability data, say so rather than guessing.
- Reply with ONLY JSON: {{"summary": "<1-2 plain sentences>", "recommendations": [{{"space_id": <int>, "reason": "<one sentence, cite free window>"}}]}} with at most 3 items (0 if nothing fits)."""

def _run_tool(conn, args, today, affiliation):
    try:
        args = {k: v for k, v in args.items() if k != "require_availability"}
        p = SearchParams(**args, affiliation=affiliation)
    except (ValidationError, TypeError) as e:
        return {"error": f"bad arguments: {e}"}
    return {"results": search(conn, p, today)}

async def recommend(conn, query, today, now, dates, affiliation=None, client=None, max_rounds=4):
    if not config.OPENROUTER_API_KEY and client is None: raise RuntimeError("OPENROUTER_API_KEY is not set")
    msgs = [{"role": "system", "content": system_prompt(today, now, dates)}, {"role": "user", "content": query[:1000]}]
    seen, own = {}, client is None
    client = client or httpx.AsyncClient(timeout=45)
    try:
        for _ in range(max_rounds):
            r = await client.post(config.OPENROUTER_URL, headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}", "X-Title": "ReserveCMU"},
                                  #json={"model": config.OPENROUTER_MODEL, "messages": msgs, "tools": [TOOL], "temperature": 0.2})
                                  json={"model": config.OPENROUTER_MODEL, "messages": msgs, "tools": [TOOL], "temperature": 0.2, "max_tokens": 1000})
            if r.status_code >= 400: raise ProviderError(f"OpenRouter {r.status_code}: {_provider_msg(r)}")
            body = r.json()
            if "error" in body: raise ProviderError("OpenRouter: " + str(body["error"].get("message", body["error"]))[:200])
            m = body["choices"][0]["message"]
            if m.get("tool_calls"):
                msgs.append(m)
                for tc in m["tool_calls"]:
                    try: args = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError: args = {}
                    res = _run_tool(conn, args, today, affiliation)
                    for s in res.get("results", []): seen[s["id"]] = s
                    msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(res)})
                continue
            return _finalize(m.get("content") or "", seen)
        return {"summary": "I couldn't narrow this down. Try adding a time or group size.", "recommendations": []}
    finally:
        if own: await client.aclose()

def _finalize(text, seen):
    mt = re.search(r"\{.*\}", text, re.S)
    try: data = json.loads(mt.group(0)) if mt else {}
    except json.JSONDecodeError: data = {}
    recs = []
    for x in data.get("recommendations", [])[:3]:
        s = seen.get(x.get("space_id"))
        if s: recs.append({"space": s, "reason": str(x.get("reason", ""))[:300]})   # drops hallucinated ids
    return {"summary": str(data.get("summary", ""))[:400] or "Here's what I found.", "recommendations": recs}
