"""Public version-one robot API models."""

import re
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
PlatformName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SafeUpdateWindow(StrictModel):
    """An absolute, half-open interval [start, end) in UTC."""

    start: AwareDatetime
    end: AwareDatetime

    @field_validator("start", "end", mode="before")
    @classmethod
    def require_iso_string(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
            value,
        ):
            raise ValueError("timestamp must be ISO-8601 with timezone")
        return value

    @field_validator("start", "end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def chronological(self) -> "SafeUpdateWindow":
        if self.start >= self.end:
            raise ValueError("start must precede end")
        return self


class DailySafeUpdateWindow(StrictModel):
    """A daily half-open wall-clock interval; the registry does not schedule it."""

    start_local: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    end_local: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: str = Field(min_length=1, max_length=128)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        if value == "localtime" or value.startswith(("posix/", "right/")):
            raise ValueError("timezone must be an IANA zone name")
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be an available IANA zone name") from exc
        return value

    @model_validator(mode="after")
    def nonempty(self) -> "DailySafeUpdateWindow":
        if self.start_local == self.end_local:
            raise ValueError("start_local and end_local must differ")
        return self


class RobotCreate(StrictModel):
    platform: PlatformName
    hardware_profile: dict[str, Any]
    site: Name
    cohort_labels: list[Name] = Field(default_factory=list, max_length=32)
    safe_update_window: SafeUpdateWindow | DailySafeUpdateWindow | None = None


class RobotPatch(StrictModel):
    platform: PlatformName | None = None
    hardware_profile: dict[str, Any] | None = None
    site: Name | None = None
    cohort_labels: list[Name] | None = Field(default=None, max_length=32)
    safe_update_window: SafeUpdateWindow | DailySafeUpdateWindow | None = None


class ReportedOut(StrictModel):
    current_digest: Digest | None
    previous_digest: Digest | None
    agent_version: str | None
    last_seen: datetime | None
    health_summary: dict[str, Any]
    heartbeat_sequence: int


class DesiredOut(StrictModel):
    target_digest: Digest | None
    set_by: str | None
    reason: str | None
    set_at: datetime | None


class RobotOut(StrictModel):
    id: UUID
    platform: str
    hardware_profile: dict[str, Any]
    site: str
    cohort_labels: list[str]
    safe_update_window: SafeUpdateWindow | DailySafeUpdateWindow | None
    created_at: datetime
    updated_at: datetime
    reported_state: ReportedOut
    desired_state: DesiredOut


class EnrollmentOut(StrictModel):
    robot: RobotOut
    credential: str | None


class RobotList(StrictModel):
    items: list[RobotOut]
    next_cursor: str | None


class Heartbeat(StrictModel):
    sequence: int = Field(ge=1, le=9223372036854775807)
    current_digest: Digest | None = None
    previous_digest: Digest | None = None
    agent_version: PlatformName
    health_summary: dict[str, Any] = Field(default_factory=dict)


class DesiredUpdate(StrictModel):
    target_digest: Digest | None
    set_by: Name
    reason: Name


class ErrorDetails(StrictModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(StrictModel):
    error: ErrorDetails
