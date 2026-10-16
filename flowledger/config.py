"""Explicit local deployment settings. No application secrets are persisted."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database: Path = field(default_factory=lambda: Path(os.getenv("FLOW_DB", "data/flow.db")))
    token: str = field(default_factory=lambda: os.getenv("FLOW_API_TOKEN", ""))
    demo_mode: bool = field(default_factory=lambda: os.getenv("FLOW_DEMO_MODE", "0") == "1")
    hosts: tuple[str, ...] = field(default_factory=lambda: tuple(
        x.strip() for x in os.getenv("FLOW_HOSTS", "127.0.0.1,localhost,testserver").split(",") if x.strip()
    ))
    lease_seconds: int = field(default_factory=lambda: int(os.getenv("FLOW_LEASE_SECONDS", "30")))
    retry_base_seconds: float = 2.0
    max_attempts: int = 3
    max_replays: int = 2
    max_jobs: int = 1000
    report: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent / "reports/verification.json")

    def __post_init__(self):
        if self.token and len(self.token) < 24:
            raise ValueError("FLOW_API_TOKEN must contain at least 24 characters.")
        if not 1 <= self.lease_seconds <= 300:
            raise ValueError("Lease duration must be between 1 and 300 seconds.")
        if not 0 < self.retry_base_seconds <= 60 or not 1 <= self.max_attempts <= 10:
            raise ValueError("Invalid bounded retry configuration.")
        if not 0 <= self.max_replays <= 10 or not 1 <= self.max_jobs <= 10_000:
            raise ValueError("Invalid workspace capacity configuration.")
