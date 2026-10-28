"""Onboarding / offboarding orchestration across identity, endpoint and access tools.

Every step is isolated: a failure is recorded, dependent steps are skipped and
the run continues so the final alert always carries the full picture.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from .alerts import Alerter
from .gws import GoogleWorkspaceClient
from .mdm import JamfClient
from .models import Employee, StepResult, StepStatus, WorkflowResult, utcnow
from .okta import OktaClient
from .onepassword import OnePasswordClients

log = logging.getLogger(__name__)
T = TypeVar("T")


class _Step:
    """Helper returned by ``LifecycleWorkflow._run``; carries the value and the result."""

    def __init__(self, result: StepResult, value: Any):
        self.result = result
        self.value = value

    @property
    def ok(self) -> bool:
        return self.result.status is StepStatus.SUCCESS


class LifecycleWorkflow:
    def __init__(
        self,
        *,
        okta: OktaClient,
        gws: GoogleWorkspaceClient,
        onepassword: OnePasswordClients,
        mdm: JamfClient,
        alerter: Alerter,
        modes: dict[str, str] | None = None,
        okta_offboard_action: str = "suspend",
        gws_org_unit_path: str = "/",
        shared_vaults: list[str] | None = None,
        mdm_invitation_days: int = 30,
    ):
        self.okta = okta
        self.gws = gws
        self.op = onepassword
        self.mdm = mdm
        self.alerter = alerter
        self.modes = modes or {}
        self.okta_offboard_action = okta_offboard_action
        self.gws_org_unit_path = gws_org_unit_path
        self.shared_vaults = shared_vaults or ["Engineering"]
        self.mdm_invitation_days = mdm_invitation_days

    # ------------------------------------------------------------------ onboard
    def onboard(self, employee: Employee) -> WorkflowResult:
        result = WorkflowResult("onboarding", employee.login, modes=self.modes)
        log.info("=== Onboarding %s (%s) ===", employee.display_name, employee.login)

        # 1. Okta: create user, assign group
        user = self._run(
            result,
            "okta",
            "create_user",
            lambda: self.okta.create_user(employee),
            detail=lambda u: f"id={u['id']} status={u['status']}",
        )
        if user.ok:
            self._run(
                result,
                "okta",
                "assign_group",
                lambda: self._okta_assign_group(user.value["id"], employee.group),
                detail=lambda g: f"added to '{g['profile']['name']}' ({g['id']})",
            )
        else:
            self._skip(result, "okta", "assign_group", "okta.create_user failed")

        # 2. Google Workspace: create account
        self._run(
            result,
            "google_workspace",
            "create_user",
            lambda: self.gws.create_user(employee, org_unit_path=self.gws_org_unit_path),
            detail=lambda u: (
                f"{u['primaryEmail']} created (orgUnit={u.get('orgUnitPath', '/')}, "
                "must change password at next login)"
            ),
        )

        # 3. 1Password: create user, add to vault group, verify vaults
        op_user = self._run(
            result,
            "1password",
            "create_user",
            lambda: self.op.scim.create_user(employee),
            detail=lambda u: f"scim id={u['id']}",
        )
        if op_user.ok:
            self._run(
                result,
                "1password",
                "assign_vault_group",
                lambda: self._op_assign_group(op_user.value["id"], employee.group),
                detail=lambda g: f"added to group '{g['displayName']}'",
            )
        else:
            self._skip(result, "1password", "assign_vault_group", "1password.create_user failed")
        if self.op.connect:
            self._run(
                result,
                "1password",
                "verify_shared_vaults",
                lambda: self.op.connect.resolve_vaults(self.shared_vaults),
                detail=lambda vaults: (
                    "vaults: " + ", ".join(f"{v['name']} ({v['id'][:8]}...)" for v in vaults)
                ),
            )

        # 4. MDM enrollment invitation
        self._run(
            result,
            "mdm",
            "send_enrollment_invitation",
            lambda: self.mdm.send_enrollment_invitation(employee, expires_in_days=self.mdm_invitation_days),
            detail=lambda inv: (
                f"Jamf invitation id={inv['id']} emailed to {inv['email_addresses']} "
                f"(expires {inv['expiration_date']})"
            ),
        )

        # 5. Alert
        self._finish(result)
        return result

    # ----------------------------------------------------------------- offboard
    def offboard(self, email: str) -> WorkflowResult:
        email = email.lower()
        result = WorkflowResult("offboarding", email, modes=self.modes)
        log.info("=== Offboarding %s ===", email)

        # 1. Okta: kill sessions, suspend/deactivate
        user = self._run(
            result,
            "okta",
            "lookup_user",
            lambda: self.okta.get_user(email),
            detail=lambda u: f"id={u['id']} status={u['status']}",
        )
        if user.ok:
            uid = user.value["id"]
            self._run(
                result,
                "okta",
                "clear_sessions",
                lambda: self.okta.clear_sessions(uid),
                detail=lambda _: "all Okta sessions revoked",
            )
            if self.okta_offboard_action == "deactivate":
                self._run(
                    result,
                    "okta",
                    "deactivate",
                    lambda: self.okta.deactivate_user(uid),
                    detail=lambda _: "user DEPROVISIONED",
                )
            else:
                self._run(
                    result,
                    "okta",
                    "suspend",
                    lambda: self.okta.suspend_user(uid),
                    detail=lambda _: "user SUSPENDED",
                )
        else:
            self._skip(result, "okta", "clear_sessions", "okta.lookup_user failed")
            self._skip(result, "okta", self.okta_offboard_action, "okta.lookup_user failed")

        # 2. Google Workspace: revoke OAuth tokens, sign out, suspend
        self._run(
            result,
            "google_workspace",
            "revoke_oauth_tokens",
            lambda: self.gws.revoke_all_tokens(email),
            detail=lambda revoked: (
                f"revoked {len(revoked)} token(s): " + ", ".join(r["app"] or r["clientId"] for r in revoked)
                if revoked
                else "no active OAuth tokens"
            ),
        )
        self._run(
            result,
            "google_workspace",
            "sign_out",
            lambda: self.gws.sign_out(email),
            detail=lambda _: "all sessions invalidated",
        )
        self._run(
            result,
            "google_workspace",
            "suspend",
            lambda: self.gws.suspend_user(email),
            detail=lambda u: f"suspended={u.get('suspended')}",
        )

        # 3. 1Password: remove from all groups (shared vaults), suspend, audit
        op_user = self._run(
            result,
            "1password",
            "lookup_user",
            lambda: self._op_find_user(email),
            detail=lambda u: f"scim id={u['id']} active={u.get('active')}",
        )
        if op_user.ok:
            op_uid = op_user.value["id"]
            self._run(
                result,
                "1password",
                "remove_from_shared_vaults",
                lambda: self.op.scim.remove_user_from_all_groups(op_uid),
                detail=lambda groups: (
                    f"removed from {len(groups)} group(s): {', '.join(groups)}"
                    if groups
                    else "no group memberships"
                ),
            )
            self._run(
                result,
                "1password",
                "suspend",
                lambda: self.op.scim.suspend_user(op_uid),
                detail=lambda u: f"active={u.get('active')}",
            )
            if self.op.events:
                self._run(
                    result,
                    "1password",
                    "audit_events",
                    lambda: self.op.events.events_for_user(op_uid),
                    detail=lambda events: (
                        f"{len(events)} audit event(s) recorded: "
                        + ", ".join(sorted({e["action"] for e in events}))
                    ),
                )
        else:
            for action in ("remove_from_shared_vaults", "suspend"):
                self._skip(result, "1password", action, "1password.lookup_user failed")

        # 4. Alert
        self._finish(result)
        return result

    # ----------------------------------------------------------------- helpers
    def _okta_assign_group(self, user_id: str, group_name: str) -> dict[str, Any]:
        group = self.okta.find_group(group_name)
        self.okta.add_user_to_group(user_id, group["id"])
        return group

    def _op_assign_group(self, user_id: str, group_name: str) -> dict[str, Any]:
        group = self.op.scim.find_group(group_name.title())
        self.op.scim.add_user_to_group(group["id"], user_id)
        return group

    def _op_find_user(self, email: str) -> dict[str, Any]:
        user = self.op.scim.find_user(email)
        if not user:
            raise LookupError(f"1password: no SCIM user with userName {email!r}")
        return user

    def _run(
        self,
        result: WorkflowResult,
        service: str,
        action: str,
        fn: Callable[[], T],
        *,
        detail: Callable[[T], str] | None = None,
    ) -> _Step:
        started = time.perf_counter()
        step = StepResult(service, action, StepStatus.SUCCESS)
        value: Any = None
        try:
            value = fn()
            step.detail = detail(value) if detail else ""
            if isinstance(value, dict):
                step.data = {k: value[k] for k in ("id", "status", "active", "primaryEmail") if k in value}
            log.info("[%s] %s: %s", service, action, step.detail or "ok")
        except Exception as exc:  # every failure must be captured for the alert
            step.status = StepStatus.FAILED
            step.error = f"{type(exc).__name__}: {exc}"
            log.error("[%s] %s FAILED: %s", service, action, step.error)
        step.duration_ms = int((time.perf_counter() - started) * 1000)
        result.steps.append(step)
        return _Step(step, value)

    @staticmethod
    def _skip(result: WorkflowResult, service: str, action: str, reason: str) -> None:
        log.warning("[%s] %s skipped: %s", service, action, reason)
        result.steps.append(StepResult(service, action, StepStatus.SKIPPED, detail=f"skipped: {reason}"))

    def _finish(self, result: WorkflowResult) -> None:
        result.finished_at = utcnow()
        self._run(
            result,
            "alert",
            "send_summary",
            lambda: self.alerter.send(result),
            detail=lambda r: f"{r['channel']} alert delivered",
        )
        result.finished_at = utcnow()
        log.info(
            "=== %s for %s finished: %s (%d step(s) failed) ===",
            result.workflow,
            result.subject,
            "OK" if result.ok else "WITH FAILURES",
            len(result.failures),
        )
