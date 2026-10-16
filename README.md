# FlowLedger

[![Verify FlowLedger](https://github.com/Debotaro/flow-ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/Debotaro/flow-ledger/actions/workflows/ci.yml)

Support automation must survive a duplicate event, a delayed approval and a worker crash. FlowLedger turns typed support events into durable local case-management actions, with an inspectable queue and audit history.

This is a complete bounded single-workspace service with an independent worker process and operator console. Routing is deterministic. Its connector writes real SQLite case records; it does not send messages, issue refunds, lock customer accounts or call external tools.

![FlowLedger operations queue](docs/screenshots/queue-desktop.png)

## Run locally

Requires Python 3.12 or newer. No API key, model download or paid service is needed.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -c requirements.lock -e '.[dev]'
$env:FLOW_DEMO_MODE = '1'
python -m flowledger --port 8012
```

On Linux/macOS activate with `source .venv/bin/activate` and use `export FLOW_DEMO_MODE=1`. Open `http://127.0.0.1:8012`. Load the clearly fictional examples, inspect an ordinary event, process a due job, and approve or reject a refund/security review. Demo mode explicitly enables synthetic failure injection; leave it disabled for ordinary event intake.

For continuous processing, open another terminal with the same environment/database and run:

```powershell
python -m flowledger.worker
```

The API's **Process next due job** control is useful for inspecting transitions one at a time. The separate worker demonstrates actual durable polling and recovery without an in-process API background task.

## Reliable execution

- Validated, typed events and a unique idempotency key. Same key/body returns the existing job; same key/different body produces a conflict.
- Event, outbox intent and initial audit are persisted atomically. Refund and security events wait for an explicit owner decision before execution.
- Due jobs are claimed transactionally with leases and fencing. An expired worker cannot acknowledge a job that another worker owns.
- Transient failures retry within a bounded attempt budget; exhaustion enters a dead-letter queue with an explicit replay decision.
- The local case connector has a unique effect identity. Recovery after an effect commits but before acknowledgement avoids a second local case.
- Queue state, attempts, due times, errors, decisions, effects and audits remain inspectable after a process restart.

This does **not** promise exactly-once delivery to arbitrary external systems. A future remote connector needs its own idempotency contract and reconciliation. Decision actors are owner-supplied labels, not independently verified identities.

## Verification

```powershell
python -m pytest -q --cov=flowledger
python -m flowledger.verify
cd ui-tests
npm ci
npx playwright install chromium
npm test
```

The verification command runs isolated real SQLite workflows with controlled fault/clock injection and writes `reports/verification.json`. It does not mutate the user's workspace database. The UI reads that checked-in report. See [verification evidence](docs/VERIFICATION.md) for the executed results, exact scenarios and limits. Fault-injection measurements are not customer outcomes or business savings.

## Architecture and operation

FastAPI/Pydantic provides typed JSON contracts; SQLite WAL stores events, durable outbox jobs, audit and case effects. The static same-origin operator console reads actual state. A separate worker claims and executes due work. See [architecture](docs/ARCHITECTURE.md), [security](SECURITY.md), [operations](docs/OPERATIONS.md) and [API contract](API_CONTRACT.md).

A non-root container, local Compose configuration, dependency lock and GitHub Actions workflow are included. Hosted CI has executed the backend/browser checks and built and run the image: thirteen Compose smoke checks passed, including independent worker progress and persistence across service restarts. See the [executed release record](docs/VERIFICATION.md#public-release-and-hosted-container-execution). A public application deployment remains pending an authorized persistent host, HTTPS, a workspace token and an explicit allowed hostname.

Personal portfolio project for Deboraj Sarkar, developed with Codex-assisted implementation and executable review. It demonstrates system behavior, not prior automation employment or independently assessed individual proficiency. See [engineering walkthrough](docs/WALKTHROUGH.md). Code is MIT licensed.
