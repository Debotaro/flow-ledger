import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from flowledger.app import create_app
from flowledger.config import Settings
from flowledger.models import EventInput
from flowledger.service import seed, tick
from flowledger.store import Conflict, StaleClaim, Store


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def settings(tmp_path):
    return Settings(database=tmp_path / "workflow.db", demo_mode=True)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(settings, clock):
    return Store(settings, clock)


def event(**kwargs):
    return EventInput(subject="Fictional support request", description="A fictional customer needs help with a sample order.", category="delivery", **kwargs)


def add(store, key="test-event", **kwargs):
    return store.intake(key, event(**kwargs))["job"]


def test_intake_is_canonical_idempotent_durable_and_atomic(store, settings, clock):
    first = store.intake("same-key", event())
    same = store.intake("same-key", event())
    assert not first["duplicate"] and same["duplicate"] and same["job"] == first["job"]
    with pytest.raises(Conflict):
        store.intake("same-key", event(priority="high"))
    restarted = Store(settings, clock)
    assert restarted.detail(first["job"]["id"])["job"] == first["job"]
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 2


def test_failed_audit_rolls_back_event_and_outbox(store, monkeypatch):
    def fail_audit(*args):
        raise sqlite3.OperationalError("Injected storage failure")

    monkeypatch.setattr(store, "_audit", fail_audit)
    with pytest.raises(sqlite3.OperationalError):
        add(store)
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_concurrent_intake_only_creates_one_event(store):
    ready = threading.Barrier(8)

    def intake(_):
        ready.wait(timeout=5)
        return store.intake("concurrent-event", event())

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(intake, range(8)))
    assert sum(not r["duplicate"] for r in results) == 1
    assert len({r["job"]["id"] for r in results}) == 1
    assert len(store.detail(results[0]["job"]["id"])["audit"]) == 2


def test_concurrent_workers_claim_and_effect_once(store):
    job = add(store)
    ready = threading.Barrier(8)

    def process(i):
        ready.wait(timeout=5)
        return tick(store, f"worker-{i}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(process, range(8)))
    assert sum(r["processed"] for r in results) == 1
    assert len(store.cases()) == 1
    detail = store.detail(job["id"])
    assert detail["job"]["attempts"] == 1 and detail["job"]["status"] == "succeeded"
    assert [r["event"] for r in detail["audit"]].count("claimed") == 1
    assert "lease_token" not in str(detail)


@pytest.mark.parametrize("category", ["refund", "security"])
def test_sensitive_routes_wait_for_explicit_approval(store, category):
    body = event().model_copy(update={"category": category})
    job = store.intake("approval-event", body)["job"]
    assert job["status"] == "pending_approval" and not tick(store)["processed"]
    assert store.cases() == []
    approved = store.decide(job["id"], "approve", "Self asserted owner", "Reviewed this fictional request")
    assert approved["status"] == "queued"
    assert tick(store)["job"]["status"] == "succeeded"
    with pytest.raises(Conflict):
        store.decide(job["id"], "reject", "Owner", "Too late to decide")


def test_rejection_has_no_effect_and_cannot_replay(store):
    job = store.intake("reject-event", event().model_copy(update={"category": "refund"}))["job"]
    assert store.decide(job["id"], "reject", "Owner", "Not approved")["status"] == "rejected"
    assert not tick(store)["processed"] and store.cases() == []
    with pytest.raises(Conflict):
        store.replay(job["id"], "Owner", "Try again")


def test_concurrent_approval_is_atomic(store):
    job = store.intake("decision-race", event().model_copy(update={"category": "security"}))["job"]

    def decide(_):
        try:
            return store.decide(job["id"], "approve", "Owner", "Reviewed")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(r is not None for r in pool.map(decide, range(4))) == 1
    assert [a["event"] for a in store.detail(job["id"])["audit"]].count("approved") == 1


def test_transient_retry_respects_exact_due_time(store, clock):
    job = add(store, failure_mode="once")
    first = tick(store)["job"]
    assert first["status"] == "retry_wait" and first["attempts"] == 1
    assert not tick(store)["processed"]
    clock.advance(1.999)
    assert not tick(store)["processed"]
    clock.advance(0.001)
    second = tick(store)["job"]
    assert second["status"] == "succeeded" and second["attempts"] == 2
    assert len(store.cases()) == 1 and store.detail(job["id"])["job"]["last_error"] is None


def exhaust(store, clock):
    assert tick(store)["job"]["status"] == "retry_wait"
    clock.advance(2)
    assert tick(store)["job"]["status"] == "retry_wait"
    clock.advance(4)
    return tick(store)["job"]


def test_exhaustion_replay_budget_and_immutable_event_identity(store, clock):
    job = add(store, failure_mode="always")
    dead = exhaust(store, clock)
    assert dead["status"] == "dead_letter" and dead["attempts"] == 3 and dead["next_attempt_at"] is None
    assert not tick(store)["processed"]
    for replay in (1, 2):
        queued = store.replay(job["id"], "Owner", "Inspect and retry this fictional job")
        assert queued["replay_count"] == replay and queued["attempts"] == 0
        assert queued["id"] == job["id"] and queued["event_key"] == job["event_key"] and queued["action"] == job["action"]
        assert exhaust(store, clock)["status"] == "dead_letter"
    with pytest.raises(Conflict, match="replay limit"):
        store.replay(job["id"], "Owner", "Another replay")
    assert store.cases() == []
    assert len([a for a in store.detail(job["id"])["audit"] if a["event"] == "claimed"]) == 9


def test_concurrent_replay_is_atomic(store, clock):
    job = add(store, failure_mode="always")
    exhaust(store, clock)

    def replay(_):
        try:
            return store.replay(job["id"], "Owner", "Retry after review")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(r is not None for r in pool.map(replay, range(4))) == 1
    assert store.detail(job["id"])["job"]["replay_count"] == 1


def test_expired_lease_fences_effect_ack_and_failure(store, clock):
    job = add(store)
    stale = store.claim("old-worker")
    clock.advance(30)
    for operation in (store.apply_case, store.acknowledge, store.fail):
        with pytest.raises(StaleClaim):
            operation(stale)
    before = len(store.audit())
    replacement = store.claim("replacement-worker")
    assert replacement.token != stale.token and replacement.job["attempts"] == 2
    for operation in (store.apply_case, store.acknowledge, store.fail):
        with pytest.raises(StaleClaim):
            operation(stale)
    assert len(store.audit()) == before + 2
    store.apply_case(replacement)
    assert store.acknowledge(replacement)["status"] == "succeeded"
    assert len(store.cases()) == 1 and store.cases()[0]["job_id"] == job["id"]


@pytest.mark.parametrize("attempt", [1, 3])
def test_durable_effect_survives_crash_gap_even_on_final_attempt(store, clock, attempt):
    job = add(store)
    for _ in range(attempt - 1):
        store.claim("crashed-without-effect")
        clock.advance(30)
    claim = store.claim("crashed-after-effect")
    case = store.apply_case(claim)  # Commit effect, deliberately omit acknowledgement.
    restarted = Store(store.settings, clock)
    clock.advance(30)
    result = tick(restarted)["job"]
    assert result["status"] == "succeeded" and result["attempts"] == attempt
    assert restarted.cases() == [case] and result["id"] == job["id"]
    assert any(a["event"] == "effect_reconciled" for a in restarted.detail(job["id"])["audit"])
    with pytest.raises(StaleClaim):
        store.acknowledge(claim)


def test_expired_leases_without_effect_exhaust_attempts(store, clock):
    job = add(store)
    for _ in range(3):
        assert store.claim().token is not None
        clock.advance(30)
    recovered = store.claim()
    assert recovered.token is None and recovered.job["status"] == "dead_letter"
    assert recovered.job["attempts"] == 3 and store.cases() == []
    assert store.detail(job["id"])["job"]["lease_until"] is None


def test_connector_deduplicates_same_effect_and_replay(store, clock):
    job = add(store)
    claim = store.claim()
    case = store.apply_case(claim)
    assert store.apply_case(claim) == case
    store.fail(claim)  # Simulated failure after effect commit.
    clock.advance(2)
    second = store.claim()
    store.fail(second)
    clock.advance(4)
    store.fail(store.claim())
    store.replay(job["id"], "Owner", "Reconcile an already committed local effect")
    assert tick(store)["job"]["status"] == "succeeded"
    assert store.cases() == [case]


def test_job_quota_audit_append_only_and_seed_mode(settings, clock):
    limited = Store(replace(settings, max_jobs=1), clock)
    job = add(limited)
    with pytest.raises(Conflict, match="capacity"):
        add(limited, "over-capacity")
    assert limited.intake("test-event", event())["duplicate"]
    with limited.connection() as db, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM audit WHERE job_id=?", (job["id"],))
    other = Store(replace(settings, database=settings.database.parent / "not-demo.db", demo_mode=False), clock)
    assert seed(other) == 3 and seed(other) == 0
    with pytest.raises(ValueError, match="DEMO_MODE"):
        add(other, "invalid-fault", failure_mode="once")


def test_api_lifecycle_schema_and_validation(settings, clock):
    with TestClient(create_app(settings, clock)) as client:
        body = event().model_dump()
        assert client.post("/api/events", json=body).status_code == 422
        created = client.post("/api/events", json=body, headers={"Idempotency-Key": "api-example"}).json()
        assert created["job"]["status"] == "queued" and not created["duplicate"]
        assert client.post("/api/events", json=body, headers={"Idempotency-Key": "api-example"}).json()["duplicate"]
        assert client.post("/api/events", json=body | {"category": "invalid"}, headers={"Idempotency-Key": "bad-event"}).status_code == 422
        assert client.post("/api/events", json=body | {"url": "https://example.com"}, headers={"Idempotency-Key": "bad-event"}).status_code == 422
        assert client.post("/api/events", json=body | {"subject": " "}, headers={"Idempotency-Key": "bad-event"}).status_code == 422
        assert client.get("/api/jobs?status=invalid").status_code == 422
        assert client.get("/api/jobs/missing").status_code == 404
        assert client.post("/api/worker/tick").json()["job"]["status"] == "succeeded"
        detail = client.get("/api/jobs/" + created["job"]["id"]).json()
        assert detail["case"]["event_key"] == "api-example" and len(detail["audit"]) == 5
        assert client.get("/api/overview").json()["counts"]["succeeded"] == 1
        assert client.get("/api/verification").status_code == 200


def test_auth_origin_host_remote_and_body_boundaries(settings):
    token = "test-token-for-flowledger-security-boundaries"
    with TestClient(create_app(replace(settings, token=token))) as client:
        assert client.get("/api/health").status_code == 200
        for path in ("/api/jobs", "/api/overview", "/api/cases", "/api/audit", "/api/verification"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer " + token}).status_code == 200
        assert client.post("/api/worker/tick").status_code == 401
        assert client.post("/api/seed", headers={"Origin": "http://["}).status_code == 403
        assert client.post("/api/seed", headers={"Origin": "https://testserver"}).status_code == 403
        assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
        assert client.post("/api/events", content=b"x" * 64_001).status_code == 413
        assert "frame-ancestors 'none'" in client.get("/api/health").headers["content-security-policy"]
    with TestClient(create_app(settings), client=("203.0.113.12", 5000)) as client:
        assert client.get("/api/overview").status_code == 403
    with pytest.raises(ValueError, match="24"):
        replace(settings, token="short")
