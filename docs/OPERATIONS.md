# Operation and deployment

## Local workflow

The API and independent worker must use the same FLOW_DB path. Prefer an absolute path in a managed service. Start the API with `python -m flowledger --port 8012` and the worker with `python -m flowledger.worker` in separate terminals. The manual tick endpoint handles at most one due job per request and is protected like other owner operations.

The default installation binds to loopback. Set FLOW_DEMO_MODE=1 only to load fictional fixtures or exercise controlled failures. In ordinary mode synthetic failure requests are rejected. Health reports the configured mode so the UI can explain available controls.

| Setting | Purpose |
| --- | --- |
| FLOW_DB | Persistent SQLite database shared by API and worker |
| FLOW_API_TOKEN | Workspace bearer token, required for remote access |
| FLOW_HOSTS | Exact allowed API/browser hostnames |
| FLOW_DEMO_MODE | Explicit seed/failure demonstration switch; default off |

Consult the configuration source for lease/retry/capacity defaults and permitted overrides. Changing a lease does not make a slow external side effect safe; connector idempotency and fencing still matter.

## Container and hosting

The supplied image runs as UID 10001. Compose creates separate API and worker services on the same named SQLite volume, exposes the API on host loopback only, and requires an explicit token. No paid service is needed for local execution. Container files have been prepared; see the verification report for whether Docker execution was possible in this environment.

Public deployment is an explicit approval step. Use persistent disk with correct filesystem locking, HTTPS, a managed restart policy, secret injection, allowed hosts and reverse-proxy request limits. Do not horizontally scale multiple hosts over an unsuitable shared SQLite filesystem. Do not point the connector at real customer, financial or messaging systems without a new authorization and reliability review.

## Failures and recovery

Inspect queue counts, job detail, attempt history, last safe error, lease/due times and case effects. A retry_wait job is not due immediately; the independent worker or a later manual tick will claim it. A running job with an expired lease can be reclaimed. A dead-letter job requires an explicit reviewed replay; replay retains its effect identity and audit history.

For a service restart, preserve FLOW_DB plus its WAL/SHM state and let the worker recover expired leases. Avoid moving or deleting database files while either process runs. For backup use SQLite's online backup API or stop both services first; copying only the live main file can miss WAL data. Back up encryption keys and access configuration through an appropriate separate secret process.

Liveness is not proof that a worker is polling or that a job can complete. Inspect due work and perform a harmless known-case workflow after deployment. The checked-in experiment report uses small local fixtures and controlled faults; it is not a throughput or availability SLO.
