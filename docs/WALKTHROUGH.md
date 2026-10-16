# Engineering walkthrough

Practice these tasks without assistance before claiming independent proficiency. Implementation and tests were developed with Codex assistance; the repository demonstrates application behavior.

1. Submit the same idempotency key twice, then change the payload and explain why the third request conflicts.
2. Show the transaction that persists the event and outbox together. Explain the failure it prevents and what it cannot guarantee about an external provider.
3. Create a refund review, run a worker and prove the job does not execute before approval. Explain why an actor label is not an authenticated identity.
4. Run two workers and trace claim ownership. Explain how lease expiry and fencing prevent stale acknowledgement.
5. Stop execution after the local effect commits but before acknowledgement, recover the lease, and show one local case. Explain why arbitrary email/payment APIs need their own idempotency contracts.
6. Cause transient and permanent failures with explicit demo fixtures. Inspect retries, dead letter and replay without erasing history.
7. Add a new fixed local routing action and a meaningful failing-then-passing test. Explain resource limits and the design changes needed for distributed or multi-tenant operation.
