# Design notes, trade-offs and compliance reasoning

This document explains *why* the automation is shaped the way it is, what was
deliberately left out, and how the decisions map to the obligations of a
regulated, AI-native fintech.  It is written to be read alongside the code; the
README covers *how* to run it.

## 1. Scope: what is real, what is simulated, what is missing

| Area | Status | Notes |
|------|--------|-------|
| Okta create / group / sessions / suspend / deactivate | create + group assignment **verified live** on 2026-10-28 (see `docs/VERIFICATION.md`); offboarding pending | Okta Integrator Free Plan org, SSWS token |
| Google Workspace create / token revocation / sign-out / suspend | implemented, **unverified live** | Needs a Workspace domain with a domain-wide-delegated service account |
| 1Password user + group membership (SCIM), vault lookup (Connect), audit (Events) | implemented, **unverified live** | Needs 1Password Business with the SCIM bridge deployed |
| Jamf Pro enrollment invitation | **mocked by design** | Live path exists but has never talked to a Jamf tenant |
| Slack / email alert | implemented, **unverified live** | A webhook URL is enough to verify |
| Unit tests, lint, CI | real | 47 tests, ruff clean, GitHub Actions on 3 Python versions |

Not built, on purpose (see section 5): retries and rate-limit handling,
rollback of a half-finished onboarding, the *mover* (role change) workflow,
HRIS-triggered execution, an approval gate before destructive actions, secrets
manager integration, and Intune.

## 2. Architecture decisions

### 2.1 Mock at the HTTP transport, not inside the business logic

Each client talks to a `Transport`.  `LiveTransport` wraps `requests`;
`MockTransport` routes the same requests to in-memory handlers that behave
like a small stateful tenant (users you create can then be looked up,
suspended, listed in groups, and so on).

*Alternatives considered:* `if mock: return fixture` branches inside each
method (cheap, but the production code path is never exercised by tests), or
patching `requests` per test with the `responses` library (fine for tests,
useless for a runnable demo).

*Why this one:* the exact same client code runs in tests, in the demo and in
production, so a mock-mode run is evidence about the real code, not about a
parallel implementation.  The cost is ~400 lines of mock tenant code and the
risk that the mock drifts from the vendor API.  That risk is accepted and
flagged; it is the reason the README refuses to call the live paths "verified".

### 2.2 Vendor REST APIs directly, not vendor SDKs

`okta-sdk-python`, `google-api-python-client` and the 1Password SDKs were not
used.  One uniform transport keeps the dependency surface small (three runtime
dependencies), makes every call visible and loggable, and keeps the mocks
simple.  The cost is giving up the SDKs' built-in pagination, retry and
rate-limit handling.  For the SCIM group listing I implemented pagination by
hand; retries are deliberately absent (section 5).

### 2.3 Offboarding continues on failure; dependent steps are skipped

Every step runs in its own `try/except`.  A failure is recorded with the error,
steps that depend on it are marked *skipped*, the run continues, and the alert
is always sent.

*Trade-off:* fail-fast gives a cleaner mental model and avoids acting on
inconsistent state.  For offboarding that is the wrong default: if Google is
down, Okta sessions must still be killed and 1Password access still removed.
Maximal revocation beats tidiness.  The exit code (1) and the failures section
of the alert make partial completion impossible to miss, and re-running is
safe because every revocation is idempotent.

For onboarding the same pattern is kept for consistency, with the opposite
justification: partial provisioning is harmless (the hire simply cannot log in
somewhere yet), creates are idempotent, and the engineer re-runs after fixing
the cause.  A rollback would add complexity for a problem a re-run solves.

### 2.4 Order of revocation: identity first, then sessions, then SaaS

Okta is the SSO front door, so it goes first: revoke all Okta sessions, *then*
suspend.  Session revocation precedes suspension because a suspended user with
live sessions can keep working until the session expires.  Google tokens are
revoked explicitly rather than relying on suspension alone, because
third-party OAuth grants (Slack, Zoom, AI assistants, CLI tools) are the
long-lived credentials most likely to outlive a password reset.  1Password
comes last because its contents are only reachable once the user is
authenticated anyway.

### 2.5 Okta: suspend by default, deactivate on request

`suspend` is reversible, keeps group and app assignments, and keeps the user's
logs attributable.  `deactivate` strips assignments and starts the path to
deletion.  In a regulated environment a departure is often also an
investigation or a legal hold, so the safe default is suspend-now,
deactivate-after-the-retention-window.  `OKTA_OFFBOARD_ACTION=deactivate` is
available for the second step.  Nothing in this tool deletes anything.

### 2.6 1Password: SCIM for membership, Connect and Events as read-only helpers

The brief named Connect or Events.  Neither can change who has access to a
vault: Connect manages vaults and items on behalf of a service account, and
Events is a read-only audit feed.  Vault access in 1Password is granted to
*groups*, and group membership is provisioned through the SCIM bridge.  So
SCIM does the work, Connect verifies the shared vaults behind the group exist
(and records their IDs), and Events pulls the audit events that prove the
suspension happened.  Removing the user from *every* group rather than a
configured list is deliberate: the risk is a forgotten group, not an extra
removal.

### 2.7 Secrets and credentials

All secrets come from environment variables through one `Settings` object,
are wrapped in a `Secret` type that never prints, and query strings with
`token=`/`key=` are redacted from logs.  Temporary passwords are generated with
the `secrets` module and never logged or stored; Google forces a change at
first login and Okta sends its own activation flow.

This is the right *interface*; a `.env` file is not the right *store* for
production.  The intended deployment injects the variables from a secrets
manager (AWS Secrets Manager, Vault, 1Password itself via a service account)
at runtime, with the automation's own identity scoped to the minimum roles
(section 3.3).

### 2.8 Alerts: webhook over bot token, Block Kit over plain text

An incoming webhook needs no OAuth app, no scopes and no user token, which is
the lowest-privilege way to post into one channel.  The trade-off is no
threading, no acknowledgement button and no channel choice at runtime.  The
message is structured (user, timestamp, duration, per-step status, failures,
live/mock modes) so a human can act on it and a SIEM can parse it.  SMTP email
exists for teams whose alerting channel is a ticket queue.

### 2.9 Jamf Classic API with XML

Computer invitations only exist in the Classic API, which speaks XML.  The
client obtains a bearer token from the modern Jamf Pro API first (basic auth
is deprecated for Classic calls) and logs the exact XML payload and both
responses, which is what the brief asked for.

## 3. Compliance reasoning for a regulated, AI-native fintech

### 3.1 Which controls this automates

| Control framework | Requirement | How the tool contributes |
|-------------------|-------------|--------------------------|
| SOC 2 CC6.2 / CC6.3 | Register and authorise new users; remove access when no longer required | Scripted joiner and leaver with per-step evidence |
| ISO/IEC 27001:2022 A.5.18 | Provision, review, modify and remove access rights | Same; group-based access so rights follow the role |
| PCI DSS 4.0 req. 8.2.5 | Immediately revoke access for terminated users | Sessions and OAuth tokens revoked in the same run, not just the account suspended |
| PCI DSS 4.0 req. 7.2 | Least privilege by job role | Access is granted through the `engineering` group, never per user |
| Local regulators (e.g. CBUAE, DFSA/DIFC, ADGM, or the equivalent where the firm is licensed) | Timely access revocation, auditable JML process, outsourcing/third-party controls | Evidence artefacts below; the tool is itself a third-party-free piece of code the firm owns |

The frameworks above are the ones most fintechs are audited against; the
specific regulator depends on the licence and is deliberately not asserted.

### 3.2 Evidence an auditor can use

Every run produces three artefacts: the structured JSON result (`--json`),
the Slack or email alert, and, for 1Password, the vendor's own audit events
pulled after the fact.  The JSON record contains who (subject), when (UTC
start and finish), what (each action with its vendor-side detail) and what
failed.  For audit purposes it should be written to an immutable store (the
HR ticket, a SIEM, or object storage with retention) rather than left on a
laptop.  The alert is intentionally *always* sent, even on failure, so a
missed revocation is a visible event rather than a silent one.

### 3.3 Least privilege for the automation itself

The automation's credentials are the most sensitive thing in the system.  The
recommended scoping is: Okta an OAuth service app (or a token held by a
dedicated admin) limited to `okta.users.manage` and `okta.groups.manage`;
Google a service account whose domain-wide delegation is restricted to the
two Directory scopes the code requests; 1Password a SCIM bridge token;
Jamf an API role that can only create computer invitations; Slack a single
webhook.  None of these can read mailboxes, vault contents or device data.

### 3.4 Segregation of duties and approval

The tool executes; it does not decide.  In production the trigger should be
the HRIS termination event or an approved ticket, and the ticket ID should be
carried into the run record (not yet implemented, section 5).  Suspension is
reversible and can be fully automated.  Deactivation and any future deletion
should sit behind a human approval, because they are hard to reverse and
interact with legal hold.

### 3.5 Data protection

The only personal data the tool handles is names, emails and vendor IDs.
They appear in logs and alerts, so the log sink and the Slack channel are in
scope for the firm's data-protection obligations (GDPR, the UAE PDPL, the
DIFC/ADGM data-protection regimes, or whichever applies).  Nothing is deleted,
so retention and legal-hold requirements are not pre-empted by the automation.

### 3.6 AI-native considerations

Three realities of an AI-native company shaped the design:

1. **AI tools hold long-lived grants.**  Assistants, agents and coding tools
   are authorised through OAuth against Google Workspace and similar
   platforms.  That is why token revocation is a first-class offboarding step
   rather than a side effect of suspension.  Provider API keys (LLM vendors,
   vector stores) are *not* covered by any of the five integrations here and
   belong on the offboarding checklist as a gap.
2. **Automation may be invoked by agents.**  The CLI is deterministic, has no
   model in the control path, validates its configuration before touching any
   API, exits with distinct codes, and produces a machine-readable record.
   That makes it safe to call from an agent or a workflow engine, while the
   human-approval boundary for destructive actions (3.4) stays outside the
   tool.
3. **Mocks are first-class.**  Because agents and CI will run this far more
   often than a human will, a fully offline mode with realistic behaviour is
   not a convenience but a control: it keeps development and testing away
   from production identity systems.

## 4. How it was verified

* 47 unit tests cover every public client method, the configuration
  validation, the alert payloads, the orchestration (including a forced
  Google failure and an unknown-user path), and the CLI exit codes.
* `ruff check` and `ruff format --check` pass; GitHub Actions runs lint, tests
  and the demo on Python 3.11, 3.12 and 3.13.
* The demo was run end-to-end and the logged payloads were read by hand
  against the vendor documentation.
* Okta onboarding was run against a real Integrator Free Plan org; the
  command, vendor-side identifiers and console screenshots are in
  `docs/VERIFICATION.md`.  Each further live run is appended there.
* The remaining live paths (Okta offboarding, Slack, Google, 1Password) are
  flipped to live one service at a time, following the README.

## 5. Known limitations and what I would do next

In rough priority order:

1. **Finish verifying live paths**: Okta offboarding and a Slack webhook,
   then Google and 1Password trials, updating `docs/VERIFICATION.md` each
   time.
2. **Retries and rate limits.**  Okta returns 429 with `X-Rate-Limit-Reset`;
   Google returns 403/429 with a retry hint.  Add bounded exponential backoff
   in `LiveTransport` and treat the remaining failures as today.
3. **Ticket reference and approver** carried into the run record and alert,
   and an explicit approval gate for `deactivate`.
4. **HRIS trigger** (Workday/BambooHR/HiBob webhook) so offboarding starts
   from the system of record, not from a human remembering to run a command.
5. **Mover workflow**: group changes on role change, which is where most
   privilege creep happens.
6. **Secrets manager integration** instead of `.env`, and a dedicated
   service identity per integration.
7. **Coverage gaps**: LLM-provider API keys, GitHub/GitLab, cloud IAM, Intune
   for Windows fleets, application-specific passwords in Google.
8. **Concurrency guard**: two simultaneous runs for the same user are
   harmless today because every step is idempotent, but a lock would make
   that explicit.
