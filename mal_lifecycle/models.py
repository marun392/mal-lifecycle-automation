"""Domain models shared by the workflow and the alerting layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class Employee:
    first_name: str
    last_name: str
    email: str
    group: str = "engineering"
    title: str | None = None
    department: str | None = None

    @property
    def login(self) -> str:
        return self.email.lower()

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class StepStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StepResult:
    service: str
    action: str
    status: StepStatus
    detail: str = ""
    error: str | None = None
    duration_ms: int = 0
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"{self.service}.{self.action}"


@dataclass
class WorkflowResult:
    workflow: str
    subject: str
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    steps: list[StepResult] = field(default_factory=list)
    modes: dict[str, str] = field(default_factory=dict)

    @property
    def failures(self) -> list[StepResult]:
        return [s for s in self.steps if s.status is StepStatus.FAILED]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or utcnow()
        return (end - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "subject": self.subject,
            "ok": self.ok,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": round(self.duration_seconds, 3),
            "modes": self.modes,
            "steps": [{**asdict(s), "status": s.status.value, "name": s.name} for s in self.steps],
        }
