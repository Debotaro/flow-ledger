# FlowLedger contract and acceptance

Project two began only after EvidenceDesk acceptance and local commits. A bounded single-workspace support automation service with actual local case-management effects, transactional outbox, idempotency, approvals, retry/dead-letter and lease recovery. Deterministic routing, not AI classification. No external messages, payments, accounts or network tool actions.

## API

Same-origin JSON; optional `Authorization: Bearer <FLOW_API_TOKEN>`, 24+ characters; unset is loopback-only. Use `sessionStorage` for user-entered token. Root page and public health are readable. Use EvidenceDesk's security boundary behavior without making one repository import the other. Static files live in flowledger/static, served at /assets.

- GET /api/health => {status:'ok',version:'1.0.0',auth_required:bool,demo_mode:bool,lease_seconds:number,max_attempts:number}
- GET /api/overview => {counts:{queued:int,running:int,retry_wait:int,pending_approval:int,succeeded:int,dead_letter:int,rejected:int},cases:int,audit_events:int}
- GET /api/jobs?status=<optional exact status> => {jobs:[job]}; latest100.
- GET /api/jobs/{id} => {job:job,audit:[{id,job_id,event,actor,detail,created_at}],case:object|null}
- POST /api/events header Idempotency-Key:<unique key> body {subject:string,description:string,category:'delivery'|'refund'|'security'|'general',priority:'low'|'normal'|'high',failure_mode:'none'|'once'|'always' default none}. Server canonicalizes validated body and hashes it. Same key/same body returns same job with duplicate:true. Same key/different body409. Original returns{job,duplicate:false}. Fault injection only accepted when FLOW_DEMO_MODE=1 (default local demo true, deployment false); UI labels synthetic failure simulation explicitly. Root/agent can refine default but must align health/UI/docs.
- POST /api/jobs/{id}/decision body {decision:'approve'|'reject',actor:string,note:string}. Only pending_approval; server records self-asserted actor (not verified user identity); returns {job}. Refund/security categories require approval by policy. Other events queue automatically. All actual effects are local case records.
- POST /api/jobs/{id}/replay body {actor:string,note:string} => {job}. Only dead_letter; bounded replays, reset attempt budget but retain complete audit and dedupe identity. No duplicate local case on replay.
- POST /api/worker/tick => {processed:bool,job:job|null}. Executes at most one due job, including recovered expired leases. Owner control for an inspectable demonstration; also implement actual independent worker CLI `python -m flowledger.worker` polling durable outbox. No background thread in API process.
- GET /api/cases => {cases:[{id,job_id,event_key,subject,category,priority,action,created_at}]}
- GET /api/audit => {events:[audit]}; latest200, no delete API.
- POST /api/seed => {added:int}; fixed fictional examples idempotent: ordinary delivery, refund needing approval, security needing approval, general once failure. Add permanent failure if useful; demo labels/no real customers.
- GET /api/verification => checked-in reports/verification.json or {status:'not_run'}.

job = {id,event_key,subject,description,category,priority,action,status,attempts,max_attempts,next_attempt_at,lease_until,last_error,replay_count,created_at,updated_at,failure_mode}. Use UTC ISO strings for visible timestamps, nullable where not applicable. Backend may have private lease tokens; never return them in API or audit. Error {detail:string|list};422 input,401 auth,403origin/remote,409state/key conflict,413body limit.

## Invariants and acceptance

Store intake event, outbox job and audit atomically. Unique event key. Transactional claims under SQLite write locks, lease token fencing and bounded attempts. Crash between durable case effect and job acknowledgement must recover safely using unique job/event effect identity. The local connector effect and final job acknowledgement may be separate commits deliberately to exercise recovery. Do not claim universal exactly-once network delivery. Audit every transition; no arbitrary tool names/URLs from events. Bound payloads, job capacity and histories/API pagination. Make worker polling stoppable and due times monotonic enough with injected clock tests. Preserve state on restart. Retry errors are safe strings, no secrets/payload dumps.

Test duplicate/conflict/concurrent intake, concurrent workers, transient retry, exhaustion, approval/rejection, stale leases/fencing, worker crash after effect, replay, persistence, malformed input/auth/origin and quotas. Include an executable experiment CLI `python -m flowledger.verify` writing reports/verification.json with exact measured scenarios, counts, environment, elapsed times and scope; isolate its DB in a temporary directory. Tests may inject clocks and failures but report what is simulated. CLI experiments must execute genuine SQLite state transitions, not hard-coded report values.

UI: polished operations console with live queue/status filters, event intake dialog, job detail/audit trail, approval/reject and dead-letter replay dialogs with actor/reason, run-one control, local case list, actual verification evidence and good empty/loading/error states. All seven statuses distinguish textually. Refresh/poll bounded, no invented throughput metrics. Desktop/mobile and keyboard; render untrusted text safely. Root owns docs/infra; backend agent Python/tests/fixtures/report, UI agent static/ui-tests.
