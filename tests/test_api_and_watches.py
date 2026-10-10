import json
import time

from fastapi.testclient import TestClient

from agentheon.api import create_app

PLAN = {"status": "ok", "skills": ["docs"], "inputs": {"topic": "pricing"}}
LOOKUP = {"action": "tool", "tool": "lookup", "args": {"topic": "pricing"}}


def answer(text):
    return {"action": "answer", "answer": text, "sources": ["doc://pricing"]}


def test_ready_and_skills(make_platform):
    platform, _ = make_platform()
    client = TestClient(create_app(platform))
    ready = client.get("/api/v1/ready").json()
    assert ready["status"] == "ok" and ready["skills"] >= 3
    names = {s["name"] for s in client.get("/api/v1/skills").json()}
    assert {"docs", "calculator", "web_research"} <= names
    assert client.get("/").status_code == 200


def test_auth_required_when_keys_set(make_platform):
    platform, _ = make_platform(PLAN, LOOKUP, answer("20."), auth_dev_mode=False,
                                api_keys={"secret-key": ("alice", "user")})
    client = TestClient(create_app(platform))
    assert client.get("/api/v1/skills").status_code == 401
    assert client.get("/api/v1/skills", headers={"Authorization": "Bearer wrong"}).status_code == 401
    good = {"Authorization": "Bearer secret-key"}
    assert client.get("/api/v1/me", headers=good).json() == {"id": "alice", "role": "user"}
    assert client.post("/api/v1/admin/knowledge/purge", headers=good).status_code == 403
    result = client.post("/api/v1/query", json={"question": "Pro price?"}, headers=good).json()
    assert result["status"] == "succeeded"


def test_sync_query_and_runs(make_platform):
    platform, _ = make_platform(PLAN, LOOKUP, answer("Pro costs 20."))
    client = TestClient(create_app(platform))
    result = client.post("/api/v1/query", json={"question": "Pro price?"}).json()
    assert result["status"] == "succeeded" and result["answer"] == "Pro costs 20."
    fetched = client.get(f"/api/v1/query/result/{result['run_id']}").json()
    assert fetched["answer"] == "Pro costs 20."
    assert client.get("/api/v1/runs").json()[0]["id"] == result["run_id"]
    assert client.get("/api/v1/query/result/does-not-exist").status_code == 404


def test_stream_emits_steps_then_result(make_platform):
    platform, _ = make_platform(PLAN, LOOKUP, answer("Pro costs 20."))
    client = TestClient(create_app(platform))
    with client.stream("POST", "/api/v1/query/stream", json={"question": "Pro price?"}) as response:
        body = "".join(response.iter_text())
    events = [line[7:] for line in body.splitlines() if line.startswith("event: ")]
    assert events[0] == "started" and "plan" in events and "tool_call" in events and events[-1] == "result"
    last = json.loads(body.strip().splitlines()[-1][6:])
    assert last["status"] == "succeeded"


def test_submit_then_poll(make_platform):
    platform, _ = make_platform(PLAN, LOOKUP, answer("Pro costs 20."))
    with TestClient(create_app(platform)) as client:
        run_id = client.post("/api/v1/query/submit", json={"question": "Pro price?"}).json()["run_id"]
        for _ in range(50):
            result = client.get(f"/api/v1/query/result/{run_id}").json()
            if result["status"] != "running":
                break
            time.sleep(0.05)
    assert result["status"] == "succeeded"


async def test_watch_detects_change_only_when_answer_changes(make_platform):
    platform, llm = make_platform(
        LOOKUP, answer("Pro costs 20."),  # run 1: baseline (skills given, so no planner)
        LOOKUP, answer("Pro  costs 20."),  # run 2: same after normalizing -> no judge call
        LOOKUP, answer("Pro costs 25."),  # run 3: changed -> judge
        {"severity": "notable", "summary": "Price rose from 20 to 25."},
    )
    watches = platform.watches
    watch = watches.create(owner="alice", name="Pro price", question="Pro price?", skills=["docs"],
                           interval_minutes=60, threshold="notable")

    first = await watches.run_once(watch["id"])
    assert first["severity"] == "none" and "baseline" in first["summary"]
    second = await watches.run_once(watch["id"])
    assert second["severity"] == "none" and second["notified"] is False
    third = await watches.run_once(watch["id"])
    assert third["severity"] == "notable" and third["notified"] is True
    assert "20 to 25" in third["summary"]
    assert llm.replies == []
    # the judge saw earlier runs, not just the last one
    assert "Earlier answer 1" in llm.calls[-1][1]["content"]
    assert [r["severity"] for r in watches.runs(watch["id"])] == ["notable", "none", "none"]


async def test_scheduler_claims_due_watches_once_and_respects_max_runs(make_platform):
    platform, _ = make_platform(LOOKUP, answer("Pro costs 20."))
    watches = platform.watches
    watch = watches.create(owner="alice", name="w", question="Pro price?", skills=["docs"],
                           interval_minutes=60, max_runs=1)
    assert await watches.tick() == 1
    assert await watches.tick() == 0  # next run is an hour away
    assert watches.get(watch["id"])["active"] is False  # max_runs reached


def test_watch_api_and_ownership(make_platform):
    platform, _ = make_platform(auth_dev_mode=False,
                                api_keys={"ka": ("alice", "user"), "kb": ("bob", "user")})
    client = TestClient(create_app(platform))
    alice, bob = {"Authorization": "Bearer ka"}, {"Authorization": "Bearer kb"}
    bad = client.post("/api/v1/watches", json={"name": "x", "question": "q", "threshold": "huge"}, headers=alice)
    assert bad.status_code == 422
    created = client.post("/api/v1/watches", json={"name": "Pro", "question": "Pro price?", "skills": ["docs"]},
                          headers=alice).json()
    assert client.get(f"/api/v1/watches/{created['id']}", headers=bob).status_code == 404
    assert client.post(f"/api/v1/watches/{created['id']}/pause", headers=alice).json()["active"] is False
    assert [w["id"] for w in client.get("/api/v1/watches", headers=alice).json()] == [created["id"]]
    assert client.get("/api/v1/watches", headers=bob).json() == []
    assert client.delete(f"/api/v1/watches/{created['id']}", headers=alice).status_code == 204
