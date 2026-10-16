"""Run an independent polling worker against the durable SQLite outbox."""

import argparse
import json
import signal
import sqlite3
import threading
import uuid

from .config import Settings
from .service import tick
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Run the FlowLedger local-case outbox worker")
    parser.add_argument("--once", action="store_true", help="Execute at most one due job and exit")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--worker-id", default="worker-" + uuid.uuid4().hex[:8])
    parser.add_argument("--crash-after-effect", action="store_true", help="Synthetic abrupt-exit fault; requires demo mode")
    args = parser.parse_args()
    if not 0.05 <= args.poll_interval <= 60:
        parser.error("Poll interval must be between 0.05 and 60 seconds.")
    if not 2 <= len(args.worker_id) <= 80 or not args.worker_id.strip():
        parser.error("Worker id must contain 2–80 visible characters.")
    settings = Settings()
    if args.crash_after_effect and not settings.demo_mode:
        parser.error("Crash simulation requires FLOW_DEMO_MODE=1.")
    store, stopped = Store(settings), threading.Event()

    def stop(signum, frame):
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopped.is_set():
        try:
            result = tick(store, args.worker_id, args.crash_after_effect)
            if args.once or result["processed"]:
                # Logs describe execution without copying event descriptions or customer identifiers.
                job = result["job"]
                summary = {"processed": result["processed"], "job": None if job is None else {
                    key: job[key] for key in ("id", "status", "attempts", "replay_count")
                }}
                print(json.dumps(summary), flush=True)
        except sqlite3.OperationalError:
            # Storage contention leaves the current lease intact; recovery owns the next transition.
            print(json.dumps({"status": "storage_unavailable", "detail": "Storage is temporarily unavailable."}), flush=True)
            if args.once:
                raise SystemExit(1) from None
        if args.once:
            break
        stopped.wait(args.poll_interval)


if __name__ == "__main__":
    main()
