"""Seed data shared by the mocked services.

In mock mode every service behaves like a small, consistent tenant: the same
pre-existing employees exist in Okta, Google Workspace and 1Password, so an
offboarding run against one of them exercises every code path.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from .models import Employee

MOCK_DOMAIN = "example.com"
OKTA_MOCK_DOMAIN = "dev-123456.okta.com"

SEED_EMPLOYEES: list[Employee] = [
    Employee("Bob", "Departing", f"bob.departing@{MOCK_DOMAIN}", "engineering", "Senior Engineer"),
    Employee("Carol", "Chen", f"carol.chen@{MOCK_DOMAIN}", "engineering", "Staff Engineer"),
    Employee("Dan", "Osei", f"dan.osei@{MOCK_DOMAIN}", "finance", "Controller"),
]

SEED_GROUPS: list[str] = ["everyone", "engineering", "finance", "it-admins"]


def fake_id(prefix: str, seed: str, length: int = 17) -> str:
    """Deterministic, realistic-looking identifier derived from ``seed``."""
    digest = hashlib.sha1(seed.lower().encode()).hexdigest()
    return prefix + digest[:length]


def fake_uuid26(seed: str) -> str:
    """1Password style 26-character upper-case identifier."""
    return hashlib.sha1(seed.lower().encode()).hexdigest()[:26].upper()


def iso_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
