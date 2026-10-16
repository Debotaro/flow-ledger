"""Validated intake and explicit deterministic routing policy."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal["delivery", "refund", "security", "general"]
Priority = Literal["low", "normal", "high"]
Status = Literal["queued", "running", "retry_wait", "pending_approval", "succeeded", "dead_letter", "rejected"]
STATUSES = ("queued", "running", "retry_wait", "pending_approval", "succeeded", "dead_letter", "rejected")
ACTIONS = {"delivery": "delivery_followup", "refund": "refund_review", "security": "security_review", "general": "support_triage"}
APPROVAL_REQUIRED = {"refund", "security"}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def visible(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("Must include visible text.")
        return value


class EventInput(Input):
    subject: str = Field(min_length=2, max_length=160)
    description: str = Field(min_length=10, max_length=4000)
    category: Category
    priority: Priority = "normal"
    failure_mode: Literal["none", "once", "always"] = "none"


class Attribution(Input):
    actor: str = Field(min_length=2, max_length=80)
    note: str = Field(min_length=2, max_length=500)


class Decision(Attribution):
    decision: Literal["approve", "reject"]
