# Security boundaries

Run on loopback by default. Remote access requires FLOW_API_TOKEN of at least 24 characters, HTTPS termination and an exact allowed hostname in FLOW_HOSTS. Generate a random token using Python's secrets module and pass it through the process environment or a deployment secret manager; never commit it. The browser keeps a user-entered token only in sessionStorage.

One token grants owner access to the whole workspace, including approvals and replay. This is not multi-user role-based access. Actor fields are self-asserted audit labels. Do not expose a token-backed shared workspace as a public multi-tenant product. Rotate access by changing the environment token and restarting the API; the direct worker process requires trusted filesystem access.

Host/origin checks, bounded bodies and schemas constrain the API. Source strings render as text in the client; a restrictive content policy blocks remote and inline scripts. There are no arbitrary tool, URL-fetch, shell, payment or external messaging actions. The local case connector is deliberately fixed. No secrets belong in event descriptions or audit reasons; the workspace database and its backups may contain their text.

Keep FLOW_DEMO_MODE disabled outside an explicit demonstration. It enables controlled failure modes and adds synthetic failure fixtures to the seed action. An authorized owner can explicitly load the three harmless fictional ordinary/approval examples even with demo mode off. These are visible test mechanisms, not observed third-party outages.

Approval prevents execution until a workspace owner records a decision. It does not prove an independently authenticated human approved, prevent a malicious token-holder from approving, or authorize any external financial action. SQLite files and logs require owner-controlled permissions, encryption where appropriate, retention and backups. Audit records are inspectable but not immutable against a database administrator.

Report vulnerabilities privately to the published repository owner. Do not include real user records, credentials or sensitive payloads in public issues. Automated tests and an npm audit do not establish a security certification.
