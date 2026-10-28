# mal-lifecycle-automation

Automation for the security-relevant steps of employee **onboarding** and
**offboarding** - the connective tissue between the identity, endpoint and
access tools:

| # | Capability | Service / API | Default mode | Live mode |
|---|------------|---------------|--------------|-----------|
| 1 | User lifecycle: create user, assign group, suspend/deactivate, revoke sessions | Okta Users & Groups API (`/api/v1`) | mock | **verified live** (onboarding and offboarding) against an Okta Integrator Free Plan org on 2026-10-28 - see [docs/VERIFICATION.md](docs/VERIFICATION.md) |
| 2 | Account provisioning + revoke all OAuth tokens, sign out, suspend | Google Workspace Admin SDK Directory API | mock | implemented (delegated service account or access token) - **not yet exercised against a real domain** |
| 3 | Vault-group assignment on hire, removal from all shared vaults on exit | 1Password SCIM bridge (+ Connect API, Events API read-only) | mock | implemented - **not yet exercised against a real SCIM bridge** |
| 4 | MDM enrollment invitation email | Jamf Pro (Classic API computer invitation) | mock | mocked by design; a live code path exists but is unverified |
| 5 | Structured completion alert (user, timestamp, actions, failures) | Slack incoming webhook (Block Kit) or SMTP email | mock | Slack **verified live** on 2026-10-28 - see [docs/VERIFICATION.md](docs/VERIFICATION.md); SMTP email not yet exercised |

**Honest status:** every live client is written against the vendor's documented
REST API with the exact endpoints, auth schemes and payloads listed below, and
the same client code is exercised by the unit tests through the mock transport.
Okta onboarding and offboarding, and the Slack alert, have been run against
real tenants with the evidence recorded in [docs/VERIFICATION.md](docs/VERIFICATION.md).  The other live paths
have not yet been run end-to-end; the "Which API calls are real vs. mocked"
section tells you how to do that one service at a time, and every live run is
added to the verification log.  Design decisions, trade-offs and the
compliance reasoning behind them are in [DESIGN.md](DESIGN.md).

Every integration flips between `mock` and `live` independently with a
`<SERVICE>_MODE` environment variable, so you can e.g. run Okta for real while
everything else stays mocked.  In mock mode each service is a small *stateful*
in-memory tenant with realistic request/response payloads and the same client
code path as production - the mocks sit at the HTTP transport layer, not inside
the business logic.

```
$ python -m mal_lifecycle demo

ONBOARDING ada.lovelace@example.com -> OK
  [OK  ] okta.create_user                           id=00uf535a45d0ca4a7319 status=PROVISIONED
  [OK  ] okta.assign_group                          added to 'engineering' (00gd1797d4fa2313e5f7)
  [OK  ] google_workspace.create_user               ada.lovelace@example.com created (orgUnit=/, must change password at next login)
  [OK  ] 1password.create_user                      scim id=8766F4E9B0169D523BE7BBF450
  [OK  ] 1password.assign_vault_group               added to group 'Engineering'
  [OK  ] 1password.verify_shared_vaults             vaults: Engineering (5354b995...)
  [OK  ] mdm.send_enrollment_invitation             Jamf invitation id=42 emailed to ada.lovelace@example.com (expires 2026-11-27 06:20:46)
  [OK  ] alert.send_summary                         slack alert delivered

OFFBOARDING bob.departing@example.com -> OK
  [OK  ] okta.lookup_user                           id=00u7bdde62d224ffe78e status=ACTIVE
  [OK  ] okta.clear_sessions                        all Okta sessions revoked
  [OK  ] okta.suspend                               user SUSPENDED
  [OK  ] google_workspace.revoke_oauth_tokens       revoked 3 token(s): Slack, Zoom, Google Cloud SDK
  [OK  ] google_workspace.sign_out                  all sessions invalidated
  [OK  ] google_workspace.suspend                   suspended=True
  [OK  ] 1password.lookup_user                      scim id=DB890322A00CC471F07FD5328A active=True
  [OK  ] 1password.remove_from_shared_vaults        removed from 2 group(s): Everyone, Engineering
  [OK  ] 1password.suspend                          active=False
  [OK  ] 1password.audit_events                     3 audit event(s) recorded: leave, suspend
  [OK  ] alert.send_summary                         slack alert delivered
```

## Quick start (fully mocked, no accounts needed)

```bash
git clone https://github.com/<you>/mal-lifecycle-automation.git
cd mal-lifecycle-automation
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # defaults are all MODE=mock
python -m mal_lifecycle demo  # onboard Ada Lovelace, offboard Bob Departing
```

Run either workflow on its own:

```bash
# new hire
python -m mal_lifecycle onboard --first Grace --last Hopper \
    --email grace.hopper@example.com --group engineering \
    --title "Software Engineer" --department Engineering --json run-result.json

# departing employee (mock tenant knows bob.departing@, carol.chen@, dan.osei@example.com)
python -m mal_lifecycle offboard --email bob.departing@example.com
```

Flags: `--mock` forces every integration into mock mode regardless of `.env`,
`--json PATH` writes the structured run result (`-` for stdout), `-v` enables
debug logging, `--env-file` picks a different dotenv file.  The process exits
`1` if any step failed and `2` on configuration errors, so it slots into CI or
a ticketing hook.

### Tests and lint

```bash
pytest                                 # 47 unit tests, all offline
ruff check . && ruff format --check .  # lint + formatting (the repo passes clean)
```

Or `make test`, `make lint`, `make demo`.

## Configuration

All configuration comes from environment variables (a `.env` file is loaded
automatically; see [`.env.example`](.env.example)).  **No credential is ever
hard-coded.**  Secrets are wrapped in a `Secret` type so they never appear in
`repr()`/logs, and query strings containing `token=`/`key=`/... are redacted
from log lines.

| Variable | Purpose |
|----------|---------|
| `OKTA_MODE`, `OKTA_DOMAIN`, `OKTA_API_TOKEN`, `OKTA_OFFBOARD_ACTION` | Okta org + SSWS token; `suspend` (default) or `deactivate` on offboarding |
| `GWS_MODE`, `GWS_SERVICE_ACCOUNT_FILE`, `GWS_ADMIN_EMAIL`, `GWS_ACCESS_TOKEN`, `GWS_ORG_UNIT_PATH` | Google Workspace auth (service-account JSON impersonating an admin, **or** a short-lived access token) |
| `OP_MODE`, `OP_SCIM_URL`, `OP_SCIM_TOKEN` | 1Password SCIM bridge (required in live mode) |
| `OP_CONNECT_HOST`, `OP_CONNECT_TOKEN`, `OP_SHARED_VAULTS` | optional Connect server used to verify the shared vault(s) exist |
| `OP_EVENTS_HOST`, `OP_EVENTS_TOKEN` | optional Events API used to confirm audit events after offboarding |
| `MDM_MODE`, `JAMF_URL`, `JAMF_USERNAME`, `JAMF_PASSWORD`, `JAMF_INVITATION_DAYS` | Jamf Pro (mock by default) |
| `ALERT_MODE`, `ALERT_CHANNEL`, `SLACK_WEBHOOK_URL` | Slack incoming webhook |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` | email alternative (`ALERT_CHANNEL=email`) |

Live mode validates up front that the variables it needs are present and
fails fast with a `configuration error` listing what is missing.

## Which API calls are real vs. mocked

The clients are the same in both modes; only the transport differs
(`LiveTransport` = `requests`, `MockTransport` = in-process fixture handlers).

### 1. Okta - live-capable (free [developer org](https://developer.okta.com/signup/))

| Step | Call |
|------|------|
| create user (activated) | `POST /api/v1/users?activate=true` - idempotent: a `400 E0000001 "already exists"` falls back to `GET /api/v1/users/{login}` |
| assign group | `GET /api/v1/groups?q=engineering` (exact-name match) then `PUT /api/v1/groups/{gid}/users/{uid}` |
| offboard | `GET /api/v1/users/{login}` -> `DELETE /api/v1/users/{id}/sessions` -> `POST /api/v1/users/{id}/lifecycle/suspend` (or `/deactivate` when `OKTA_OFFBOARD_ACTION=deactivate`) |

Setup: create an API token under **Security > API > Tokens**, create a group
named `engineering`, then `OKTA_MODE=live OKTA_DOMAIN=dev-xxxx.okta.com OKTA_API_TOKEN=...`.

### 2. Google Workspace - live-capable (Admin SDK Directory API)

| Step | Call |
|------|------|
| create account | `POST /admin/directory/v1/users` with a random 20-char temporary password and `changePasswordAtNextLogin=true` (`409` -> `GET /users/{email}`) |
| revoke OAuth tokens | `GET /admin/directory/v1/users/{email}/tokens` then `DELETE /users/{email}/tokens/{clientId}` for each |
| sign out everywhere | `POST /admin/directory/v1/users/{email}/signOut` |
| suspend | `PUT /admin/directory/v1/users/{email}` `{"suspended": true}` |

Setup (service account): create a service account, enable **domain-wide
delegation** in the Workspace admin console for its client ID with scopes
`admin.directory.user` and `admin.directory.user.security`, download the JSON
key and set `GWS_MODE=live GWS_SERVICE_ACCOUNT_FILE=... GWS_ADMIN_EMAIL=<a super admin>`.
For a quick test with your own admin login:
`GWS_ACCESS_TOKEN=$(gcloud auth print-access-token)`.

### 3. 1Password - live-capable (SCIM bridge; Connect + Events are read-only helpers)

The brief says "Connect or Events API", but neither can change who has access
to a vault: Connect manages vaults/items for a service account, and the Events
API is a read-only audit feed.  In 1Password, vault access is granted to
**groups**, and group membership is provisioned through the
[SCIM bridge](https://support.1password.com/scim/).  So:

| Step | API | Call |
|------|-----|------|
| create user | SCIM | `POST /Users` (`409` -> `GET /Users?filter=userName eq "..."`) |
| assign vault group | SCIM | `GET /Groups?filter=displayName eq "Engineering"` then `PATCH /Groups/{id}` with an `add members` operation |
| verify shared vaults exist | Connect | `GET /v1/vaults`, resolves `OP_SHARED_VAULTS` names to IDs for the audit record |
| remove from all shared vaults | SCIM | `GET /Groups` (paged) then `PATCH /Groups/{id}` `remove members[value eq "uid"]` for every group containing the user |
| suspend | SCIM | `PATCH /Users/{id}` `{"active": false}` |
| audit confirmation | Events | `POST /api/v1/auditevents` filtered to the user's UUID |

Setup: deploy the SCIM bridge (1Password Business trial works), set
`OP_MODE=live OP_SCIM_URL=https://<bridge> OP_SCIM_TOKEN=<bearer>`; optionally
add `OP_CONNECT_HOST`/`OP_CONNECT_TOKEN` and `OP_EVENTS_TOKEN` (an Events API
token with the *audit events* feature).  Group names are title-cased
(`engineering` -> `Engineering`).

### 4. MDM (Jamf Pro) - **mocked**

The client implements the real sequence - `POST /api/v1/auth/token` (basic
auth -> bearer), `POST /JSSResource/computerinvitations/id/0` with the XML
invitation body, then `GET /JSSResource/computerinvitations/id/{id}` - and
logs the exact payload and response:

```
INFO mal_lifecycle.mdm: [jamf] POST /JSSResource/computerinvitations/id/0 payload:
<computer_invitation><invitation_type>EMAIL</invitation_type><email_addresses>ada.lovelace@example.com</email_addresses>
<expiration_date>2026-11-27 06:20:46</expiration_date><ssh_username>jamfmanagement</ssh_username>...</computer_invitation>
INFO mal_lifecycle.mdm: [jamf] response HTTP 201: <computer_invitation><id>42</id></computer_invitation>
INFO mal_lifecycle.mdm: [jamf] invitation 42:
{ "computer_invitation": { "id": 42, "invitation": "5981054734013511673", "invitation_type": "Email",
  "email_addresses": "ada.lovelace@example.com", "enrollment_status": "Pending", ... } }
```

`MDM_MODE=live` will send these calls to `JAMF_URL`, but no Jamf trial tenant
was available to verify it, so consider it mocked.

### 5. Alerts - Slack verified live (SMTP email live-capable)

`POST <SLACK_WEBHOOK_URL>` with a Block Kit message: header, fields for
**user / workflow / started (UTC) / duration**, an outcome line, the full list
of **actions taken** with ✅ / ❌ / ⏩ markers, a **Failures** section when any
step failed, and a context footer showing which services ran live vs. mock.
With `ALERT_CHANNEL=email` the same summary goes out as a plain-text email with
the JSON run result attached.  The alert is sent for both workflows and is
always sent, even when earlier steps failed.

## How the workflow behaves

* **Fault isolation** - every step runs in its own try/except; a failure is
  recorded with the error, steps that depend on it are marked *skipped*, and
  the run continues so the alert carries the complete picture.  Exit code `1`
  signals failures.
* **Idempotent creates** - re-running onboarding for an existing Okta / GWS /
  1Password user reuses the existing account instead of failing.
* **No secrets in output** - temporary passwords are generated with `secrets`
  and never logged or stored; API tokens live only in the environment.
* **Structured result** - `--json` emits one record per workflow with every
  step's status, detail, error and duration, suitable for a SIEM or ticket.

## Project layout

```
mal_lifecycle/
  cli.py          argparse entry point (python -m mal_lifecycle)
  config.py       Settings.from_env - all secrets from env vars, live-mode validation
  factory.py      builds live or mock clients from Settings
  workflow.py     LifecycleWorkflow.onboard() / .offboard() orchestration
  transport.py    LiveTransport (requests) / MockTransport (fixture routing), BaseClient
  okta.py         OktaClient + OktaMockTenant
  gws.py          GoogleWorkspaceClient + GoogleWorkspaceMockDirectory
  onepassword.py  SCIM / Connect / Events clients + OnePasswordMockTenant
  mdm.py          JamfClient + JamfMockServer
  alerts.py       Slack Block Kit + email alerters (+ mocks)
  models.py       Employee, StepResult, WorkflowResult
  mockdata.py     seed employees/groups shared by all mocks
tests/            pytest suite (one or more tests per public function)
```

## License

MIT
