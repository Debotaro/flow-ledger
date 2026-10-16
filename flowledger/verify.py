"""Isolated executable reliability experiments, including real worker subprocess crashes."""

import argparse
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings
from .models import EventInput
from .service import tick
from .store import Conflict, StaleClaim, Store, timestamp, utcnow


class ExperimentClock:
    def __init__(self):
        self.now = datetime(2026, 10, 16, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def event(category="delivery", failure="none"):
    return EventInput(subject="Fictional verification request", description="Fictional local verification data; no real customer or external effect.",
                      category=category, failure_mode=failure)


def count(store, table):
    if table not in ("jobs", "events", "cases", "audit"):
        raise ValueError("Unsupported experiment counter")
    with store.connection() as db:
        return db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]


def verify(output: Path):
    started = time.perf_counter()
    report = {
        "status": "complete", "generated_at": timestamp(utcnow()),
        "scope": "Isolated fictional SQLite/local-case experiments. Includes real worker subprocesses and abrupt exits; retry/lease boundary scenarios explicitly inject a clock. No external connector, payment, message delivery, throughput benchmark or general exactly-once guarantee.",
        "environment": {"python": platform.python_version(), "platform": platform.system(), "sqlite": sqlite3.sqlite_version},
        "settings": {"lease_seconds": 30, "retry_base_seconds": 2, "max_attempts": 3, "max_replays": 2},
        "scenarios": [],
    }
    with tempfile.TemporaryDirectory(prefix="flowledger-verify-") as folder:
        base = Settings(database=Path(folder) / "unused.db", demo_mode=True, token="", lease_seconds=30)

        def isolated(name, clock=utcnow, **settings):
            return Store(replace(base, database=Path(folder) / f"{name}.db", **settings), clock)

        def run(name, experiment):
            began = time.perf_counter()
            try:
                details = experiment()
                result = {"name": name, "passed": True, "details": details}
            except Exception as exc:  # noqa: BLE001 - preserve per-experiment failures in the report; CLI exits nonzero
                result = {"name": name, "passed": False, "details": {"error_type": type(exc).__name__}}
            result["elapsed_ms"] = round((time.perf_counter() - began) * 1000, 2)
            report["scenarios"].append(result)

        def concurrent_intake():
            store, gate = isolated("intake"), threading.Barrier(12)

            def add(_):
                gate.wait(timeout=10)
                return store.intake("shared-event-key", event())

            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(add, range(12)))
            conflicts = 0
            try:
                store.intake("shared-event-key", event("general"))
            except Conflict:
                conflicts += 1
            created = sum(not r["duplicate"] for r in results)
            assert created == count(store, "events") == count(store, "jobs") == conflicts == 1
            assert len({r["job"]["id"] for r in results}) == 1
            return {"concurrent_submissions": 12, "created_jobs": created, "duplicates": 12 - created,
                    "conflicting_payloads_rejected": conflicts, "event_rows": count(store, "events"), "clock": "system UTC"}

        run("Concurrent idempotent intake", concurrent_intake)

        def concurrent_workers():
            store, gate = isolated("workers"), threading.Barrier(8)
            for i in range(16):
                store.intake(f"worker-event-{i}", event())

            def work(i):
                gate.wait(timeout=10)
                processed = 0
                while tick(store, f"experiment-worker-{i}")["processed"]:
                    processed += 1
                return processed

            with ThreadPoolExecutor(max_workers=8) as pool:
                processed = sum(pool.map(work, range(8)))
            succeeded = store.overview()["counts"]["succeeded"]
            assert processed == succeeded == count(store, "cases") == 16
            assert sum(j["attempts"] for j in store.jobs()) == 16
            return {"worker_threads": 8, "submitted_jobs": 16, "processed_jobs": processed,
                    "succeeded_jobs": succeeded, "local_cases": count(store, "cases"), "duplicate_effects": 0}

        run("Concurrent worker claims", concurrent_workers)

        def approvals():
            store = isolated("approval")
            refund = store.intake("refund-approval", event("refund"))["job"]
            security = store.intake("security-reject", event("security"))["job"]
            assert not tick(store)["processed"] and count(store, "cases") == 0
            store.decide(refund["id"], "approve", "Fictional owner", "Reviewed the fictional refund request")
            store.decide(security["id"], "reject", "Fictional owner", "Rejected the fictional security request")
            assert tick(store)["job"]["status"] == "succeeded"
            assert not tick(store)["processed"] and count(store, "cases") == 1
            return {"pending_before_decision": 2, "effects_before_approval": 0, "approved_jobs": 1,
                    "rejected_jobs": store.overview()["counts"]["rejected"], "local_cases": count(store, "cases"),
                    "actor_identity": "self-asserted demonstration label"}

        run("Approval and rejection policy", approvals)

        def retries():
            clock = ExperimentClock()
            store = isolated("retry", clock)
            store.intake("transient-event", event(failure="once"))
            first = tick(store)["job"]
            assert first["status"] == "retry_wait" and not tick(store)["processed"]
            clock.advance(1.999)
            assert not tick(store)["processed"]
            clock.advance(0.001)
            transient = tick(store)["job"]
            store.intake("permanent-event", event(failure="always"))
            tick(store)
            clock.advance(2)
            tick(store)
            clock.advance(4)
            exhausted = tick(store)["job"]
            assert transient["status"] == "succeeded" and transient["attempts"] == 2
            assert exhausted["status"] == "dead_letter" and exhausted["attempts"] == 3
            assert count(store, "cases") == 1
            return {"clock": "injected UTC; actual SQLite transitions", "early_claims_blocked": 2,
                    "transient_attempts": transient["attempts"], "exhausted_attempts": exhausted["attempts"],
                    "retry_delays_seconds": [2, 4], "dead_letter_jobs": 1, "local_cases": count(store, "cases")}

        run("Retry due times and exhaustion", retries)

        def fencing():
            clock, rejected = ExperimentClock(), 0
            store = isolated("fencing", clock)
            store.intake("fenced-event", event())
            stale = store.claim("old-worker")
            clock.advance(30)
            replacement = store.claim("new-worker")
            before = count(store, "audit")
            for operation in (store.apply_case, store.acknowledge, store.fail):
                try:
                    operation(stale)
                except StaleClaim:
                    rejected += 1
            assert rejected == 3 and count(store, "audit") == before and count(store, "cases") == 0
            store.apply_case(replacement)
            assert store.acknowledge(replacement)["status"] == "succeeded"
            return {"clock": "injected UTC at exact 30-second lease boundary", "stale_mutations_rejected": rejected,
                    "stale_audit_writes": 0, "attempts": replacement.job["attempts"], "local_cases": count(store, "cases")}

        run("Expired lease token fencing", fencing)

        def replay():
            clock = ExperimentClock()
            store = isolated("replay", clock)
            original = store.intake("replayed-event", event())["job"]
            claim = store.claim()
            case = store.apply_case(claim)
            store.fail(claim)  # Explicit injected failure after a committed local effect.
            clock.advance(2)
            store.fail(store.claim())
            clock.advance(4)
            store.fail(store.claim())
            replayed = store.replay(original["id"], "Fictional owner", "Reconcile the existing local case")
            completed = tick(store)["job"]
            assert completed["status"] == "succeeded" and store.cases() == [case]
            assert replayed["event_key"] == original["event_key"] and replayed["id"] == original["id"]
            assert any(a["event"] == "effect_deduplicated" for a in store.audit())
            return {"fault": "injected failure after effect commit", "clock": "injected UTC",
                    "replay_count": completed["replay_count"], "local_cases": count(store, "cases"),
                    "retained_event_rows": count(store, "events"), "duplicate_effects": 0}

        run("Replay retains effect identity", replay)

        def environment(store):
            env = os.environ.copy()
            env.update({"FLOW_DB": str(store.settings.database), "FLOW_DEMO_MODE": "1", "FLOW_API_TOKEN": "",
                        "FLOW_LEASE_SECONDS": str(store.settings.lease_seconds)})
            return env

        root = Path(__file__).resolve().parent.parent

        def independent_worker():
            store = isolated("process")
            for i in range(5):
                store.intake(f"process-event-{i}", event())
            process = subprocess.Popen([sys.executable, "-m", "flowledger.worker", "--poll-interval", "0.05"],
                                       cwd=root, env=environment(store), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 15
                while store.overview()["counts"]["succeeded"] < 5 and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.05)
                assert store.overview()["counts"]["succeeded"] == count(store, "cases") == 5
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
            assert not stderr
            emitted = [json.loads(line) for line in stdout.splitlines()]
            assert sum(row.get("processed", False) for row in emitted) == 5
            return {"execution": "separate polling Python process; terminated after all acknowledgements",
                    "submitted_jobs": 5, "succeeded_jobs": store.overview()["counts"]["succeeded"],
                    "worker_output_records": len(emitted), "local_cases": count(store, "cases"), "clock": "system UTC"}

        run("Independent polling worker process", independent_worker)

        def crash_recovery(attempt):
            store = isolated(f"crash-{attempt}", lease_seconds=1)
            original = store.intake(f"crash-event-{attempt}", event())["job"]
            for _ in range(attempt - 1):
                store.claim("abandoned-before-effect")
                time.sleep(1.05)
            process = subprocess.run([sys.executable, "-m", "flowledger.worker", "--once", "--crash-after-effect"],
                                     check=False, cwd=root, env=environment(store), capture_output=True, text=True, timeout=15)
            assert process.returncode == 86 and not process.stderr
            gap = store.detail(original["id"])
            assert gap["job"]["status"] == "running" and gap["case"] is not None
            time.sleep(1.05)
            restarted = Store(store.settings)
            recovered = tick(restarted)["job"]
            assert recovered["status"] == "succeeded" and recovered["attempts"] == attempt
            assert count(restarted, "cases") == 1 and restarted.detail(original["id"])["case"]["id"] == gap["case"]["id"]
            return {"fault": "real subprocess os._exit(86) after committed case, before acknowledgement",
                    "exit_code": process.returncode, "lease_seconds_override": 1, "clock": "system UTC; real lease wait",
                    "attempts": recovered["attempts"], "status_after_restart": recovered["status"],
                    "local_cases": count(restarted, "cases"), "duplicate_effects": 0}

        run("Process crash after effect on first attempt", lambda: crash_recovery(1))
        run("Process crash after effect on final attempt", lambda: crash_recovery(3))

    report["passed"] = sum(s["passed"] for s in report["scenarios"])
    report["total"] = len(report["scenarios"])
    if report["passed"] != report["total"]:
        report["status"] = "failed"
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="Run isolated FlowLedger reliability experiments")
    parser.add_argument("--output", type=Path, default=Path("reports/verification.json"))
    args = parser.parse_args()
    report = verify(args.output)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "complete" else 1)


if __name__ == "__main__":
    main()
