"""Exercise the actual worker entry point without leaking event payloads into logs."""

import json
import os
import subprocess
import sys
from pathlib import Path

from flowledger.config import Settings
from flowledger.models import EventInput
from flowledger.store import Store


def test_worker_process_logs_operational_fields_only(tmp_path):
    store = Store(Settings(database=tmp_path / "worker.db"))
    event = EventInput(subject="Private subject example", description="Sensitive payload example not for operational logs.",
                       category="general")
    job = store.intake("private-customer-identifier", event)["job"]
    env = os.environ.copy()
    env.update({"FLOW_DB": str(store.settings.database), "FLOW_API_TOKEN": "", "FLOW_DEMO_MODE": "0"})
    result = subprocess.run([sys.executable, "-m", "flowledger.worker", "--once"],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=15, check=True)
    summary = json.loads(result.stdout)
    assert summary == {"processed": True, "job": {
        "id": job["id"], "status": "succeeded", "attempts": 1, "replay_count": 0
    }}
    assert "Sensitive payload" not in result.stdout and "private-customer" not in result.stdout
    assert not result.stderr
    assert len(store.cases()) == 1
