"""A worker drives the outbox; API requests never start background execution."""

import os

from .models import EventInput
from .store import ConnectorFailure, StaleClaim, Store

SEED_EVENTS = [
    ("delivery", "Fictional delivery follow-up", "Fictional example: investigate a delayed parcel for a sample order.", "delivery", "none"),
    ("refund", "Fictional refund review", "Fictional example: review the return policy before creating a refund review case.", "refund", "none"),
    ("security", "Fictional account security review", "Fictional example: suspicious account activity requires an owner's approval.", "security", "none"),
    ("transient", "Synthetic transient failure", "Synthetic simulation: the local connector fails once and recovers on retry.", "general", "once"),
    ("permanent", "Synthetic permanent failure", "Synthetic simulation: the local connector fails until its attempt budget is exhausted.", "general", "always"),
]


def seed(store: Store):
    added = 0
    for key, subject, description, category, failure in SEED_EVENTS:
        if failure != "none" and not store.settings.demo_mode:
            continue
        result = store.intake("fictional-seed-v1:" + key, EventInput(
            subject=subject, description=description, category=category, failure_mode=failure))
        added += not result["duplicate"]
    return added


def tick(store: Store, worker="manual-worker", crash_after_effect=False):
    if crash_after_effect and not store.settings.demo_mode:
        raise ValueError("Crash simulation requires FLOW_DEMO_MODE=1.")
    claim = store.claim(worker)
    if claim is None:
        return {"processed": False, "job": None}
    if claim.token is None:
        return {"processed": True, "job": claim.job}
    try:
        store.apply_case(claim)
        if crash_after_effect:
            # A real abrupt process exit deliberately tests the gap between effect and acknowledgement.
            os._exit(86)
        job = store.acknowledge(claim)
    except ConnectorFailure:
        try:
            job = store.fail(claim)
        except StaleClaim:
            job = store.detail(claim.job["id"])["job"]
    except StaleClaim:
        job = store.detail(claim.job["id"])["job"]
    return {"processed": True, "job": job}
