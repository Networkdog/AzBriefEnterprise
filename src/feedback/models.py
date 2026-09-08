"""Strict contracts for public feedback submissions."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,63}$")


class FeedbackCategory(str, Enum):
    """Supported feedback intents."""

    BUG = "bug"
    IMPROVEMENT = "improvement"
    REPORT_CONTEXT = "report_context"


class FeedbackRequest(BaseModel):
    """Validated browser payload accepted by the feedback API."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: FeedbackCategory
    subject: str = Field(min_length=3, max_length=160)
    details: str = Field(min_length=10, max_length=8_000)
    contact_email: str = Field(default="", max_length=254)
    report_reference: str = Field(default="", max_length=500)
    language: str = Field(default="en", min_length=2, max_length=16)
    website: str = Field(default="", max_length=200)

    @field_validator("subject", "report_reference")
    @classmethod
    def require_single_line(cls, value: str) -> str:
        """Keep compact metadata out of multi-line log and storage fields."""
        if "\r" in value or "\n" in value:
            raise ValueError("value must be a single line")
        return value

    @field_validator("contact_email")
    @classmethod
    def validate_contact_email(cls, value: str) -> str:
        """Accept an optional, conservatively shaped contact address."""
        if value and not _EMAIL_RE.fullmatch(value):
            raise ValueError("contact_email must be a valid email address")
        return value.casefold()


class FeedbackSubmission(BaseModel):
    """Immutable feedback document persisted by the control plane."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    feedback_id: str
    created_at: datetime
    category: FeedbackCategory
    subject: str
    details: str
    contact_email: str = ""
    report_reference: str = ""
    language: str = "en"


class FeedbackReceipt(BaseModel):
    """Public acknowledgement that reveals no stored feedback content."""

    accepted: bool = True
    feedback_id: str = ""
    notification_sent: bool = False
