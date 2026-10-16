# Verification record

Executed locally on 16 October 2026 with Python 3.12.14 and SQLite 3.53.1 on Windows. This is evidence of a bounded single-workspace application, not external message/payment delivery, production load or customer impact.

## Backend and installation

A fresh repository-local virtual environment installed the pinned runtime/development dependencies and built the editable package. Twenty pytest cases passed, covering atomic intake rollback, canonical duplicate/conflicting events, concurrent intake and workers, approval/rejection races, due times, bounded retry and replay, lease fencing, crash gaps, persistence, quotas, append-only audit, HTTP boundaries and actual worker log privacy. Ruff passed. Worker logs retain operational fields rather than copying event subjects, descriptions or caller keys.

## Executed reliability experiments

Run `python -m flowledger.verify`. The command uses temporary isolated databases and exits nonzero if any experiment fails. The current `reports/verification.json` records nine passing scenarios and their actual counters:

| Scenario | Observed behavior | Fault and timing scope |
| --- | --- | --- |
| Concurrent idempotent intake | 12 submissions produced 1 event/job and 11 duplicate responses; conflicting content rejected | Actual threads and SQLite, system UTC |
| Concurrent worker claims | 8 worker threads completed 16 jobs with 16 local cases and no duplicate effects | Actual claims and connector commits |
| Approval and rejection | 0 effects before decisions; 1 approved job completed and 1 rejected job stayed without an effect | Actual state transitions; actor is a self-asserted label |
| Retry due times and exhaustion | Early claims blocked; transient job succeeded at attempt 2; permanent fault entered dead letter at attempt 3 | Injected UTC clock; actual SQLite, delays 2 and 4 seconds |
| Expired lease token fencing | 3 stale effect/ack/fail operations rejected with 0 stale audit writes | Injected clock at exact 30-second boundary |
| Replay preserves effect identity | 1 original event and 1 case retained through failure, replay and successful reconciliation | Explicit injected failure after effect commit and injected clock |
| Independent polling worker | Separate Python process processed 5 jobs into 5 acknowledged local cases | Real polling process and system UTC; process stopped after success |
| Crash after effect on first attempt | Subprocess exited with code 86 after committed effect; restart recovered succeeded state and 1 case | Actual `os._exit(86)` and real lease wait; 1-second lease override |
| Crash after effect on final attempt | Same recovery at attempt 3, without spending a nonexistent fourth attempt or duplicating the case | Actual subprocess crash; 1-second lease override for the experiment |

The usual lease is 30 seconds, max attempts 3 and max replays 2. The one-second crash-experiment override keeps the reproduction short and is disclosed in the report. Thread-level concurrency and a single polling process do not establish distributed-worker throughput. A local unique effect key cannot establish exactly-once behavior for an arbitrary external API.

## Browser and delivery gates

Sixteen Playwright checks passed against the actual local API service, with a separate screenshot-capture test passing. Genuine workflow checks exercise intake/deduplication, approvals, rejection, retry, exhaustion, replay, case effects and inert markup. Four boundary checks explicitly simulate status rendering, token/session behavior, network recovery and decision conflicts; interrupted-intake response behavior is also simulated. Those simulations are not claims of real provider failures or independent authentication identities.

Automated WCAG A/AA scans and overflow checks passed in the tested desktop 1440px, mobile 390px and narrow 320px views and dialogs. These scans are not a full accessibility certification. Prettier passed. Four actual screenshots were captured and visually inspected. Two observed UI defects were fixed before acceptance: marginal text contrast and loading-button restoration that had exposed decorative arrow text in accessible names.

`reports/verification-summary.json` records the local acceptance gate. The reliability JSON contains backend experiment results; browser behavior was verified separately rather than inferred from it.

The repository includes architecture, security boundaries, setup, recovery guidance, a dependency lock, license, non-root Docker/Compose configuration and pinned CI. Docker CLI is absent in the local environment, so no container execution is claimed. Hosted CI and public deployment remain pending explicit publication approval and an authorized persistent hosting target.
