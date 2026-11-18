import json, os, tempfile
import httpx, pytest
from fastapi.testclient import TestClient

DATA = os.path.join(os.path.dirname(__file__), "..", "data")

@pytest.fixture()
def client(monkeypatch):
    path = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", path)
    from app import config, db, ingest, main
    monkeypatch.setattr(config, "DB_PATH", path)
    c = db.connect(path); ingest.ingest(f"{DATA}/raw_pages.txt", f"{DATA}/availability.txt", c); c.close()
    monkeypatch.setattr(main, "now_local", lambda: __import__("datetime").datetime(2026, 11, 14, 11, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York")))
    return TestClient(main.app)

def test_ingest_counts(client):
    m = client.get("/meta").json()
    assert m["counts"] == {"spaces": 165, "with_availability": 165}
    assert m["availability_dates"] == ["2026-11-14"]

def test_search_respects_time_and_capacity(client):
    r = client.get("/spaces", params={"min_capacity": 30, "start_time": "13:00", "end_time": "14:00"}).json()
    assert r and all(s["max_capacity"] >= 30 for s in r)
    for s in r:
        assert any(w.split("-")[0] <= "13:00" and "14:00" <= w.split("-")[1] for w in s["availability"]["free_windows"])

def test_restricted_rooms_hidden_without_affiliation(client):
    assert not any(s["restricted_to"] for s in client.get("/spaces", params={"limit": 25}).json())
    heinz = client.get("/spaces", params={"affiliation": "Heinz", "building": "Hamburg", "limit": 25, "require_availability": "false"}).json()
    assert any(s["restricted_to"] == "Heinz" for s in heinz)

def test_feedback_needs_min_reports_then_filters(client):
    sid = client.get("/spaces", params={"limit": 1}).json()[0]["id"]
    assert client.get("/spaces", params={"noise_max": 1, "limit": 25}).json()  # unverified still returned
    for _ in range(3): assert client.post("/feedback", json={"space_id": sid, "noise": 3}).status_code == 201
    ids = [s["id"] for s in client.get("/spaces", params={"noise_max": 1, "limit": 25}).json()]
    assert sid not in ids  # now verified lively, so excluded from "silent" search
    assert client.post("/feedback", json={"space_id": 999999, "noise": 1}).status_code == 404

def test_other_date_has_unknown_availability(client):
    assert client.get("/spaces", params={"date": "2026-12-01"}).json() == []
    r = client.get("/spaces", params={"date": "2026-12-01", "require_availability": "false", "limit": 3}).json()
    assert r and r[0]["availability"]["known"] is False

def _fake_openrouter(seen_calls):
    def handler(request):
        body = json.loads(request.content); seen_calls.append(body)
        if not any(m["role"] == "tool" for m in body["messages"]):
            args = {"min_capacity": 20, "start_time": "13:00", "end_time": "14:00"}
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "search_spaces", "arguments": json.dumps(args)}}]}}]})
        tool = json.loads([m for m in body["messages"] if m["role"] == "tool"][0]["content"])["results"]
        final = {"summary": "Two options.", "recommendations": [{"space_id": tool[0]["id"], "reason": "free 13:00-14:00"}, {"space_id": 424242, "reason": "invented"}]}
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "```json\n" + json.dumps(final) + "\n```"}}]})
    return handler

@pytest.mark.asyncio
async def test_agent_loop_validates_ids(client):
    from app import agent, db
    calls = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_fake_openrouter(calls)))
    conn = db.connect()
    out = await agent.recommend(conn, "room for 20 at 1pm", "2026-11-14", "11:00", {"2026-11-14": "2026-11-14T10:50:00"}, client=http)
    assert all(c["max_tokens"] == 1000 for c in calls)  # cap so small OpenRouter balances still work
    assert len(out["recommendations"]) == 1  # hallucinated id dropped
    assert out["recommendations"][0]["space"]["max_capacity"] >= 20
    assert "Girls Who Code" not in json.dumps(calls)  # event titles never reach the LLM



# ---------- 25Live sync (mocked network; serves the saved scrape pages) ----------
def _live25_mock(mode="ok"):
    from app.ingest import read_stream
    lists, avail = read_stream(f"{DATA}/raw_pages.txt"), read_stream(f"{DATA}/availability.txt")
    seen = []
    def handler(req):
        seen.append(str(req.url)); q = req.url.params; page = int(q.get("page", 1))
        if "listdata.json" in req.url.path:
            return httpx.Response(200, json=lists[page - 1])
        if mode == "html": return httpx.Response(200, text="<html>login</html>")
        if mode == "page2_500" and page == 2: return httpx.Response(500)
        if mode == "ignores_page": page = 1
        return httpx.Response(200, json=avail[page - 1])
    return handler, seen

@pytest.fixture()
def empty_db(monkeypatch):
    from app import config, db
    path = tempfile.mktemp(suffix=".db"); monkeypatch.setattr(config, "DB_PATH", path)
    from app import live25; monkeypatch.setattr(live25, "POLITE_DELAY", 0)
    c = db.connect(path); db.init(c); return c

async def _run_sync(conn, mode="ok", day="2026-11-14"):
    from app import sync
    handler, seen = _live25_mock(mode)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return await sync.sync(conn, [day], http), seen

async def test_sync_fetches_both_pages_of_both_feeds_with_exact_urls(empty_db):
    rep, seen = await _run_sync(empty_db)
    assert rep["spaces"] == 165 and rep["busy_intervals"]["2026-11-14"] == 325
    assert rep["validation"]["2026-11-14"]["missing_availability"] == []
    lists = [u for u in seen if "listdata" in u]; avs = [u for u in seen if "availabilitydata" in u]
    assert len(lists) == 2 and len(avs) == 2
    assert all("category_id=76+81+209" in u for u in lists) and "page=2" in lists[1]
    assert all("spaces_category_id=76+81+209" in u and "start_dt=2026-11-14T00:00:00" in u for u in avs)
    assert "page=" not in avs[0] and "page=2" in avs[1]          # page 1 is the user's URL verbatim
    assert empty_db.execute("SELECT COUNT(*) FROM spaces WHERE has_availability=1").fetchone()[0] == 165

@pytest.mark.parametrize("mode,msg", [("html", "non-JSON"), ("ignores_page", "duplicate")])
async def test_sync_rejects_bad_responses_and_leaves_db_untouched(empty_db, mode, msg):
    from app import live25
    with pytest.raises(live25.SyncError, match=msg): await _run_sync(empty_db, mode)
    assert empty_db.execute("SELECT COUNT(*) FROM spaces").fetchone()[0] == 0

async def test_sync_http_error_on_page2_changes_nothing(empty_db):
    await _run_sync(empty_db)
    before = empty_db.execute("SELECT COUNT(*) FROM busy").fetchone()[0]
    with pytest.raises(httpx.HTTPStatusError): await _run_sync(empty_db, "page2_500")
    assert empty_db.execute("SELECT COUNT(*) FROM busy").fetchone()[0] == before

async def test_resync_keeps_feedback_and_deactivates_vanished_spaces(empty_db):
    await _run_sync(empty_db)
    sid = empty_db.execute("SELECT id FROM spaces LIMIT 1").fetchone()[0]
    empty_db.execute("INSERT INTO feedback(space_id,noise) VALUES(?,2)", (sid,)); empty_db.execute("INSERT INTO spaces(id,short_name,formal_name,active) VALUES(999999,'OLD','Old room',1)"); empty_db.commit()
    await _run_sync(empty_db)
    assert empty_db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1
    assert empty_db.execute("SELECT active FROM spaces WHERE id=999999").fetchone()[0] == 0

async def test_refresh_loop_step_reports_status(empty_db):
    from app import refresh, config
    handler, _ = _live25_mock(); http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    import datetime; config.REFRESH_DAYS = 1
    assert await refresh.refresh_once(http, today=datetime.date(2026, 11, 14)) == 325 and refresh.status["consecutive_failures"] == 0
    handler2, _ = _live25_mock("html"); http2 = httpx.AsyncClient(transport=httpx.MockTransport(handler2))
    assert await refresh.refresh_once(http2, today=datetime.date(2026, 11, 14)) == 0 and refresh.status["consecutive_failures"] == 1
    assert empty_db.execute("SELECT COUNT(*) FROM busy").fetchone()[0] == 325   # stale data kept

def test_list_query_params_work_for_the_frontend(client):
    r = client.get("/spaces", params=[("amenities", "whiteboard"), ("amenities", "projector"), ("space_types", "Classroom"), ("limit", 100)])
    assert r.status_code == 200 and r.json()
    assert all({"whiteboard", "projector"} <= set(s["amenities"]) and s["space_type"] == "Classroom" for s in r.json())
    assert len(client.get("/spaces", params={"limit": 200, "require_availability": "false", "affiliation": "Heinz"}).json()) > 100


def test_provider_failure_returns_readable_502(client, monkeypatch):
    from app import agent, config
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "x")
    async def boom(*a, **k): raise agent.ProviderError("OpenRouter 401: No auth credentials found")
    monkeypatch.setattr(agent, "recommend", boom)
    r = client.post("/agent/recommend", json={"query": "quiet room for 4"})
    assert r.status_code == 502 and "401" in r.json()["detail"]

@pytest.mark.asyncio
async def test_agent_turns_http_error_into_provider_error(client):
    from app import agent, db
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(404, json={"error": {"message": "No endpoints found that support tool use"}})))
    with pytest.raises(agent.ProviderError, match="404.*tool use"):
        await agent.recommend(db.connect(), "room for 4", "2026-11-14", "11:00", {}, client=http)


def test_no_data_for_today_is_reported_not_silent(client, monkeypatch):
    import datetime, zoneinfo
    from app import agent, config, main
    monkeypatch.setattr(main, "now_local", lambda: datetime.datetime(2026, 11, 20, 11, 0, tzinfo=zoneinfo.ZoneInfo("America/New_York")))
    assert client.get("/meta").json()["has_current_data"] is False
    async def boom(*a, **k): raise AssertionError("LLM must not be called when there is no data")
    monkeypatch.setattr(agent, "recommend", boom); monkeypatch.setattr(config, "OPENROUTER_API_KEY", "x")
    r = client.post("/agent/recommend", json={"query": "quiet room for 4"}).json()
    assert r["recommendations"] == [] and "2026-11-14" in r["summary"] and "hasn" in r["summary"]

def test_demo_today_relabels_saved_data(tmp_path):
    from app import db, ingest, search
    c = db.connect(str(tmp_path / "d.db")); ingest.ingest(f"{DATA}/raw_pages.txt", f"{DATA}/availability.txt", c, as_date="2026-12-01")
    assert search.search(c, search.SearchParams(min_capacity=4), "2026-12-01")

def test_doctor_reports_ok_and_problems(tmp_path, monkeypatch, capsys):
    from app import config, db, ingest, doctor
    path = str(tmp_path / "x.db"); monkeypatch.setattr(config, "DB_PATH", path)
    assert doctor.main() == 1 and "no database yet" in capsys.readouterr().out
    c = db.connect(path); ingest.ingest(f"{DATA}/raw_pages.txt", f"{DATA}/availability.txt", c)   # dated 2026-11-14, so "today" has none unless demo
    c.close(); assert doctor.main() == 1 and "no availability for today" in capsys.readouterr().out
    c = db.connect(path); ingest.ingest(f"{DATA}/raw_pages.txt", f"{DATA}/availability.txt", c, as_date=__import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo(config.TIMEZONE)).date().isoformat())
    c.close(); assert doctor.main() == 0 and "OK:" in capsys.readouterr().out


def _agent_handler(first_args, final, skip_tool_first=False):
    def h(req):
        msgs = json.loads(req.content)["messages"]
        tool = [m for m in msgs if m["role"] == "tool"]
        if not tool:
            if skip_tool_first and not any("must call" in str(m.get("content")) for m in msgs):
                return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Sure, how about the library?"}}]})
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "search_spaces", "arguments": json.dumps(first_args)}}]}}]})
        results = json.loads(tool[-1]["content"])["results"]
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(final(results))}}]})
    return h

async def _ask(handler):
    from app import agent, db
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return await agent.recommend(db.connect(), "quiet room for 4", "2026-11-14", "11:00", {"2026-11-14": "x"}, client=http)

@pytest.mark.asyncio
async def test_agent_survives_null_affiliation_limit_and_string_ids(client):
    h = _agent_handler({"min_capacity": 4, "affiliation": None, "limit": None, "start_time": "14:00", "end_time": "15:00"},
                       lambda res: {"summary": "ok", "recommendations": [{"space_id": str(res[0]["id"]), "reason": "free"}]})
    out = await _ask(h)
    assert len(out["recommendations"]) == 1 and out["trace"][0]["matches"] > 0

@pytest.mark.asyncio
async def test_agent_falls_back_when_model_lists_nothing(client):
    out = await _ask(_agent_handler({"min_capacity": 4}, lambda res: {"summary": "", "recommendations": []}))
    assert len(out["recommendations"]) == 3

@pytest.mark.asyncio
async def test_agent_is_nudged_to_search_first(client):
    out = await _ask(_agent_handler({"min_capacity": 4}, lambda res: {"summary": "ok", "recommendations": [{"space_id": res[0]["id"], "reason": "x"}]}, skip_tool_first=True))
    assert len(out["recommendations"]) == 1


def test_demo_mode_loads_sample_data_on_startup(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config, main
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "d.db")); monkeypatch.setattr(config, "DEMO_MODE", True); monkeypatch.setattr(config, "DATA_DIR", DATA)
    with TestClient(main.app) as c:
        m = c.get("/meta").json()
        assert m["has_current_data"] is True and m["demo_mode"] is True
        assert len(c.get("/spaces", params={"amenities": "computers", "limit": 50}).json()) > 0
