"""Exercise the built image through isolated Compose services and a disposable volume.

Requires Docker Compose and an existing ``flowledger-verified`` image. The runner
owns only its randomly named Compose project. All intake is fictional and the
connector creates local SQLite cases; no external account or service is used.
"""

import json
import os
import secrets
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    root = Path(__file__).resolve().parent.parent
    project = "flowledger-smoke-" + uuid.uuid4().hex[:12]
    token = secrets.token_urlsafe(32)
    environment = dict(os.environ, FLOW_API_TOKEN=token)
    checks = []
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "Disposable Docker Compose API and independent worker, fictional local case effects only. No public hosting, external delivery or availability claim.",
        "checks": checks,
    }

    def command(*args):
        return subprocess.check_output(args, cwd=root, env=environment, text=True, stderr=subprocess.STDOUT).strip()

    def check(name, condition):
        if not condition:
            raise RuntimeError(name)
        checks.append({"name": name, "passed": True})

    def request(path, body=None, *, authenticated=True, key=None):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + token
        if key:
            headers["Idempotency-Key"] = key
        data = None if body is None else json.dumps(body).encode()
        req = Request("http://127.0.0.1:8012" + path, data=data, headers=headers)
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    def wait_for(predicate, description, timeout=60):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                result = predicate()
                if result:
                    return result
            except (OSError, ValueError, subprocess.CalledProcessError) as exc:
                last_error = type(exc).__name__
            time.sleep(0.25)
        raise RuntimeError(f"Timed out waiting for {description}; last error type: {last_error}")

    event = {
        "subject": "Fictional container verification",
        "description": "Fictional isolated case; no real customer, message or financial action.",
        "category": "general",
    }

    with tempfile.TemporaryDirectory(prefix="flowledger-compose-") as folder:
        override = Path(folder) / "image.yaml"
        override.write_text("services:\n  api:\n    image: flowledger-verified\n  worker:\n    image: flowledger-verified\n", encoding="utf-8")
        compose_args = ("docker", "compose", "-p", project, "-f", str(root / "compose.yaml"), "-f", str(override))

        def compose(*args):
            return command(*compose_args, *args)

        def inspect(service):
            container = compose("ps", "-q", service)
            return json.loads(command("docker", "inspect", container))[0]

        try:
            compose("up", "--detach", "--no-build")
            wait_for(lambda: request("/api/health", authenticated=False)["status"] == "ok", "API liveness")
            check("API requires workspace authentication and disables demo faults", request("/api/health", authenticated=False)["auth_required"] and not request("/api/health", authenticated=False)["demo_mode"])
            try:
                request("/api/jobs", authenticated=False)
            except HTTPError as exc:
                check("Unauthenticated protected API rejects access", exc.code == 401)
            else:
                raise RuntimeError("Protected API accepted an unauthenticated request")

            api, worker = inspect("api"), inspect("worker")
            check("API and independent worker run as UID/GID 10001", api["Config"]["User"] == worker["Config"]["User"] == "10001:10001")
            check("API and worker are distinct running containers", api["Id"] != worker["Id"] and api["State"]["Running"] and worker["State"]["Running"])
            mounts = [{m["Destination"]: m["Name"] for m in container["Mounts"] if m["Type"] == "volume"} for container in (api, worker)]
            check("Both services share the same named persistent data volume", mounts[0].get("/app/data") is not None and mounts[0].get("/app/data") == mounts[1].get("/app/data"))

            first = request("/api/events", event, key="container-first")
            job_id = first["job"]["id"]
            wait_for(lambda: request("/api/jobs/" + job_id)["job"]["status"] == "succeeded", "independent worker completion")
            check("Independent polling worker creates one actual local case", len(request("/api/cases")["cases"]) == 1)
            duplicate = request("/api/events", event, key="container-first")
            check("Duplicate intake preserves job and local effect identity", duplicate["duplicate"] and duplicate["job"]["id"] == job_id and len(request("/api/cases")["cases"]) == 1)

            pending = request("/api/events", dict(event, category="refund"), key="container-approval")["job"]
            time.sleep(1.25)  # Observe at least one default worker polling interval.
            detail = request("/api/jobs/" + pending["id"])
            check("Polling worker leaves unapproved refund without an effect", detail["job"]["status"] == "pending_approval" and detail["case"] is None)
            request("/api/jobs/" + pending["id"] + "/decision", {"decision": "approve", "actor": "Container smoke owner", "note": "Approve this fictional local case."})
            wait_for(lambda: request("/api/jobs/" + pending["id"])["job"]["status"] == "succeeded", "approved case completion")
            check("Approved local case completes through the independent worker", len(request("/api/cases")["cases"]) == 2)

            compose("restart", "api", "worker")
            wait_for(lambda: request("/api/health", authenticated=False)["status"] == "ok", "restarted API")
            check("Jobs and case effects survive API and worker restart", len(request("/api/jobs")["jobs"]) == 2 and len(request("/api/cases")["cases"]) == 2)

            compose("stop", "worker")
            queued = request("/api/events", event, key="container-worker-stopped")["job"]
            time.sleep(1.25)
            check("API persists queued work while the separate worker is stopped", request("/api/jobs/" + queued["id"])["job"]["status"] == "queued" and len(request("/api/cases")["cases"]) == 2)
            compose("start", "worker")
            wait_for(lambda: request("/api/jobs/" + queued["id"])["job"]["status"] == "succeeded", "resumed worker")
            check("Restarted worker drains previously persisted work", len(request("/api/cases")["cases"]) == 3)
            wait_for(lambda: inspect("api")["State"]["Health"]["Status"] == "healthy", "image HTTP health check")
            check("Image HTTP health check passes on the running API", inspect("api")["State"]["Health"]["Status"] == "healthy")
            report["status"] = "passed"
        except Exception as exc:
            report["status"] = "failed"
            report["failure_type"] = type(exc).__name__
            report["failure"] = str(exc).replace(token, "[redacted]")
            raise
        finally:
            # Destruction is limited to this invocation's random disposable project.
            compose("down", "--volumes", "--remove-orphans")
            report["passed"] = len(checks)
            output = root / "reports/container-smoke.json"
            output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
