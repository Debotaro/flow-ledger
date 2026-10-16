"""Short SQLite transactions own state transitions, leases and append-only audit."""

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .config import Settings
from .models import ACTIONS, APPROVAL_REQUIRED, STATUSES, EventInput


def utcnow():
    return datetime.now(UTC)


def timestamp(value):
    if value.tzinfo is None:
        raise ValueError("The worker clock must include a timezone.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class Conflict(ValueError):
    pass


class StaleClaim(Conflict):
    pass


class ConnectorFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class Claim:
    job: dict
    token: str | None
    worker: str


class Store:
    def __init__(self, settings: Settings, clock=utcnow):
        self.settings, self.clock = settings, clock
        settings.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, body_sha256 TEXT NOT NULL,
                    body_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL REFERENCES events(id),
                    event_key TEXT UNIQUE NOT NULL, subject TEXT NOT NULL, description TEXT NOT NULL,
                    category TEXT NOT NULL, priority TEXT NOT NULL, action TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued','running','retry_wait','pending_approval','succeeded','dead_letter','rejected')),
                    attempts INTEGER NOT NULL, max_attempts INTEGER NOT NULL, next_attempt_at TEXT,
                    lease_until TEXT, lease_token TEXT, last_error TEXT, replay_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, failure_mode TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS job_due ON jobs(status,next_attempt_at,lease_until);
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES jobs(id),
                    event_key TEXT UNIQUE NOT NULL, subject TEXT NOT NULL, category TEXT NOT NULL,
                    priority TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
                    event TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_job ON audit(job_id,id);
                CREATE TRIGGER IF NOT EXISTS immutable_audit_update BEFORE UPDATE ON audit
                    BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_audit_delete BEFORE DELETE ON audit
                    BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.settings.database, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def public(row):
        if row is None:
            raise KeyError("Job not found")
        # sqlite3.Row iterates values, so keys() is required here.
        return {k: row[k] for k in row.keys() if k not in ("lease_token", "event_id")}  # noqa: SIM118

    def _audit(self, db, job_id, event, actor, detail, stamp):
        db.execute("INSERT INTO audit(job_id,event,actor,detail,created_at) VALUES (?,?,?,?,?)",
                   (job_id, event, actor, detail, stamp))

    def _job(self, db, job_id):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return row

    def intake(self, key: str, event: EventInput):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", key):
            raise ValueError("Idempotency-Key must be 3–128 letters, digits, dots, colons, underscores or hyphens.")
        if event.failure_mode != "none" and not self.settings.demo_mode:
            raise ValueError("Synthetic failures require FLOW_DEMO_MODE=1.")
        body = json.dumps(event.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest, stamp = hashlib.sha256(body.encode()).hexdigest(), timestamp(self.clock())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM events WHERE event_key=?", (key,)).fetchone()
            if old:
                if old["body_sha256"] != digest:
                    raise Conflict("This idempotency key belongs to different event content.")
                job = db.execute("SELECT * FROM jobs WHERE event_id=?", (old["id"],)).fetchone()
                return {"job": self.public(job), "duplicate": True}
            if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= self.settings.max_jobs:
                raise Conflict("Workspace job capacity reached. Export and archive this workspace before adding more jobs.")
            event_id, job_id = uuid.uuid4().hex, uuid.uuid4().hex
            status = "pending_approval" if event.category in APPROVAL_REQUIRED else "queued"
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (event_id, key, digest, body, stamp))
            db.execute("""INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (job_id, event_id, key, event.subject, event.description, event.category, event.priority,
                        ACTIONS[event.category], status, 0, self.settings.max_attempts, stamp if status == "queued" else None,
                        None, None, None, 0, stamp, stamp, event.failure_mode))
            self._audit(db, job_id, "intake_received", "intake", f"Deterministic route: {ACTIONS[event.category]}.", stamp)
            self._audit(db, job_id, "approval_required" if status == "pending_approval" else "queued", "policy",
                        "Human approval required before a local case can be created." if status == "pending_approval" else "Ready for a worker.", stamp)
            return {"job": self.public(self._job(db, job_id)), "duplicate": False}

    def jobs(self, status=None):
        if status is not None and status not in STATUSES:
            raise ValueError("Unknown job status.")
        with self.connection() as db:
            sql, args = ("SELECT * FROM jobs WHERE status=?", (status,)) if status else ("SELECT * FROM jobs", ())
            return [self.public(r) for r in db.execute(sql + " ORDER BY created_at DESC,id DESC LIMIT 100", args)]

    def detail(self, job_id):
        with self.connection() as db:
            job = self.public(self._job(db, job_id))
            audit = [dict(r) for r in db.execute("SELECT * FROM audit WHERE job_id=? ORDER BY id LIMIT 200", (job_id,))]
            case = db.execute("SELECT * FROM cases WHERE job_id=?", (job_id,)).fetchone()
            return {"job": job, "audit": audit, "case": dict(case) if case else None}

    def overview(self):
        with self.connection() as db:
            counts = dict.fromkeys(STATUSES, 0)
            counts.update({r["status"]: r["n"] for r in db.execute("SELECT status,COUNT(*) AS n FROM jobs GROUP BY status")})
            return {"counts": counts, "cases": db.execute("SELECT COUNT(*) FROM cases").fetchone()[0],
                    "audit_events": db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]}

    def cases(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM cases ORDER BY created_at DESC,id DESC LIMIT 100")]

    def audit(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200")]

    def decide(self, job_id, decision, actor, note):
        stamp = timestamp(self.clock())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["status"] != "pending_approval":
                raise Conflict("Only a pending approval can receive a decision.")
            approved = decision == "approve"
            db.execute("UPDATE jobs SET status=?,next_attempt_at=?,updated_at=? WHERE id=?",
                       ("queued" if approved else "rejected", stamp if approved else None, stamp, job_id))
            self._audit(db, job_id, "approved" if approved else "rejected", actor, note, stamp)
            return self.public(self._job(db, job_id))

    def replay(self, job_id, actor, note):
        stamp = timestamp(self.clock())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["status"] != "dead_letter":
                raise Conflict("Only a dead-letter job can be replayed.")
            if row["replay_count"] >= self.settings.max_replays:
                raise Conflict("The replay limit has been reached for this job.")
            db.execute("""UPDATE jobs SET status='queued',attempts=0,next_attempt_at=?,lease_until=NULL,
                          lease_token=NULL,last_error=NULL,replay_count=replay_count+1,updated_at=? WHERE id=?""",
                       (stamp, stamp, job_id))
            self._audit(db, job_id, "replayed", actor, f"{note} (Previous run used {row['attempts']} attempts.)", stamp)
            return self.public(self._job(db, job_id))

    def claim(self, worker="worker"):
        token = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.clock()
            stamp = timestamp(current)
            row = db.execute("""SELECT * FROM jobs WHERE
                (status IN ('queued','retry_wait') AND next_attempt_at<=?) OR
                (status='running' AND lease_until<=?)
                ORDER BY COALESCE(lease_until,next_attempt_at),created_at,id LIMIT 1""", (stamp, stamp)).fetchone()
            if row is None:
                return None
            if row["status"] == "running":
                self._audit(db, row["id"], "lease_expired", "recovery", "Worker lease expired before acknowledgement.", stamp)
                case = db.execute("SELECT id FROM cases WHERE job_id=?", (row["id"],)).fetchone()
                if case or row["attempts"] >= row["max_attempts"]:
                    status = "succeeded" if case else "dead_letter"
                    error = None if case else "Worker lease expired; attempt budget exhausted."
                    db.execute("""UPDATE jobs SET status=?,next_attempt_at=NULL,lease_until=NULL,lease_token=NULL,
                                  last_error=?,updated_at=? WHERE id=?""", (status, error, stamp, row["id"]))
                    self._audit(db, row["id"], "effect_reconciled" if case else "dead_letter", "recovery",
                                "Existing durable local case acknowledged without another connector attempt." if case else error, stamp)
                    return Claim(self.public(self._job(db, row["id"])), None, worker)
            db.execute("""UPDATE jobs SET status='running',attempts=attempts+1,next_attempt_at=NULL,
                          lease_until=?,lease_token=?,updated_at=? WHERE id=?""",
                       (timestamp(current + timedelta(seconds=self.settings.lease_seconds)), token, stamp, row["id"]))
            self._audit(db, row["id"], "claimed", worker, f"Attempt {row['attempts'] + 1} of {row['max_attempts']}.", stamp)
            return Claim(self.public(self._job(db, row["id"])), token, worker)

    def _fenced(self, db, claim, stamp):
        row = self._job(db, claim.job["id"])
        if row["status"] != "running" or row["lease_token"] != claim.token or row["lease_until"] <= stamp:
            raise StaleClaim("This worker no longer owns the job lease.")
        return row

    def apply_case(self, claim):
        """Real local connector commit. Its effect is fenced and deduplicated atomically."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            stamp = timestamp(self.clock())
            row = self._fenced(db, claim, stamp)
            existing = db.execute("SELECT * FROM cases WHERE job_id=?", (row["id"],)).fetchone()
            if existing:
                self._audit(db, row["id"], "effect_deduplicated", claim.worker, "Reused the existing local case.", stamp)
                return dict(existing)
            if row["failure_mode"] == "always" or (row["failure_mode"] == "once" and row["attempts"] == 1 and row["replay_count"] == 0):
                raise ConnectorFailure("Synthetic local connector failure.")
            case_id = uuid.uuid4().hex
            db.execute("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?)",
                       (case_id, row["id"], row["event_key"], row["subject"], row["category"], row["priority"], row["action"], stamp))
            self._audit(db, row["id"], "case_created", claim.worker, "Created a durable local support case.", stamp)
            return dict(db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone())

    def acknowledge(self, claim):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            stamp = timestamp(self.clock())
            row = self._fenced(db, claim, stamp)
            if not db.execute("SELECT id FROM cases WHERE job_id=?", (row["id"],)).fetchone():
                raise Conflict("A local case must exist before acknowledgement.")
            db.execute("""UPDATE jobs SET status='succeeded',lease_until=NULL,lease_token=NULL,
                          last_error=NULL,updated_at=? WHERE id=?""", (stamp, row["id"]))
            self._audit(db, row["id"], "succeeded", claim.worker, "Local case effect acknowledged.", stamp)
            return self.public(self._job(db, row["id"]))

    def fail(self, claim):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.clock()
            stamp = timestamp(current)
            row = self._fenced(db, claim, stamp)
            dead = row["attempts"] >= row["max_attempts"]
            due = None if dead else timestamp(current + timedelta(seconds=self.settings.retry_base_seconds * 2 ** (row["attempts"] - 1)))
            error = "Synthetic local connector failure."  # Deliberately excludes exception payloads and secrets.
            db.execute("""UPDATE jobs SET status=?,next_attempt_at=?,lease_until=NULL,lease_token=NULL,
                          last_error=?,updated_at=? WHERE id=?""",
                       ("dead_letter" if dead else "retry_wait", due, error, stamp, row["id"]))
            self._audit(db, row["id"], "dead_letter" if dead else "retry_scheduled", claim.worker,
                        "Attempt budget exhausted." if dead else f"Retry due at {due}.", stamp)
            return self.public(self._job(db, row["id"]))
