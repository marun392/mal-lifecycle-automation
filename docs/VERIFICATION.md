# Live verification log

This log records every run of the automation against a **real** vendor
tenant, with the evidence captured at the time.  Anything not listed here has
only been exercised through the mock transport and the unit tests.

Conventions: one section per service, newest run first.  Evidence files live
in [`docs/evidence/`](evidence/).  API tokens used for these runs were created
for the test and are revoked afterwards; none appear in this repository.

## Okta

**Tenant:** Okta Integrator Free Plan org (`dev-*.okta.com`), created
2026-10-28, with an `Engineering` group added by hand and an SSWS API token
named `lifecycle-automation`.  All other services were left in mock mode
(`OKTA_MODE=live`, everything else `mock`).

### 2026-10-28 - onboarding: verified

Command (run on Windows 11, Python 3.13, from a fresh clone):

```
python -m mal_lifecycle --json onboard-okta.json onboard --first Test --last Hire --email marun392+testhire@gmail.com --group engineering
```

Result:

| Step | Status | Vendor-side detail |
|------|--------|--------------------|
| `okta.create_user` | OK | `POST /api/v1/users?activate=true` returned id `00u198fcrxrNHOygS698`, status `PROVISIONED` |
| `okta.assign_group` | OK | `GET /api/v1/groups?q=engineering` matched the `Engineering` group (`00g198emh5kmUsAsd698`, case-insensitive match); `PUT /groups/{gid}/users/{uid}` returned 204 |
| remaining six steps | OK | mock mode |

Run time 2.11 s (network round-trips to Okta; the mock-only demo runs in
~0.01 s).  The Slack payload footer confirms the mode split:
`modes: okta=live, google_workspace=mock, 1password=mock, mdm=mock, alerts=mock`.

Evidence:

* [`2026-10-28-okta-onboard-terminal.png`](evidence/2026-10-28-okta-onboard-terminal.png)
  - terminal output of the run, showing the live Okta identifiers.
* [`2026-10-28-okta-onboard-console.png`](evidence/2026-10-28-okta-onboard-console.png)
  - Okta admin console, **Directory > People > Test Hire**, status *Active*,
  member of `Everyone` and `Engineering`.

Observed behaviour worth noting:

* Okta sent the activation email to the new user's address; the user
  completed activation, moving the status from `PROVISIONED` to `ACTIVE`.
* Group names are matched case-insensitively, so `--group engineering`
  resolved the console-created `Engineering` group.

### 2026-10-28 - offboarding: verified

Command:

```
python -m mal_lifecycle --json offboard-okta.json offboard --email marun392+testhire@gmail.com
```

Result:

| Step | Status | Vendor-side detail |
|------|--------|--------------------|
| `okta.lookup_user` | OK | `GET /api/v1/users/marun392+testhire@gmail.com` returned id `00u198fcrxrNHOygS698`, status `ACTIVE` |
| `okta.clear_sessions` | OK | `DELETE /api/v1/users/{id}/sessions` returned 204 |
| `okta.suspend` | OK | `POST /api/v1/users/{id}/lifecycle/suspend` returned 200; user is now `SUSPENDED` |
| `google_workspace.revoke_oauth_tokens`, `sign_out`, `suspend` | FAIL | mock directory returned 404 `Resource Not Found: userKey` (see note) |
| `1password.lookup_user` | FAIL | mock SCIM bridge has no user with that `userName` (see note) |
| `1password.remove_from_shared_vaults`, `1password.suspend` | SKIPPED | dependent on the failed lookup |
| `alert.send_summary` | OK | mock Slack webhook accepted the payload listing all four failures |

Run time 2.04 s; exit code 1.

Evidence:

* [`2026-10-28-okta-offboard-terminal.png`](evidence/2026-10-28-okta-offboard-terminal.png)
  - terminal output of the run.

Note on the simulated failures: the mock Google and 1Password tenants are
in-memory and only persist for one process.  The test hire's mock accounts
were created in the earlier onboarding *process* and no longer existed when
this offboarding process started, so the mocks correctly answered 404.  The
run is still the intended demonstration of the fault-isolation design
(DESIGN.md section 2.3): the live Okta revocation completed, dependent steps
were skipped rather than attempted blindly, the alert was still sent with
every failure listed, and the exit code signalled partial completion.  A
fully green offboarding run against the mocks uses a seeded user, e.g.
`offboard --email bob.departing@example.com`.

## Google Workspace

Not yet run against a live domain.

## 1Password

Not yet run against a live SCIM bridge.

## Jamf Pro

Mocked by design; no live run planned for this submission.

## Slack / email alerts

**Tenant:** a free Slack workspace (`Mal-Lifecycle Automation`) with a Slack
app named `Lifecycle Automation` and one incoming webhook authorised for
`#all-mal-lifecycle-automation`.  Configuration: `ALERT_MODE=live`,
`ALERT_CHANNEL=slack`, `SLACK_WEBHOOK_URL=<webhook>`.  Okta was also live
for this run; Google, 1Password and MDM stayed in mock mode.

### 2026-10-28 - Slack webhook alert: verified

Command:

```
python -m mal_lifecycle --json offboard-slack.json offboard --email bob.departing@example.com
```

`bob.departing@example.com` is a user seeded in the mock tenants only, so this
run deliberately produces a mixed result: the live Okta lookup fails with a
real `404 E0000007`, the mock Google and 1Password steps succeed, and the
alert has both successes and a failure to render.

Result:

| Step | Status | Detail |
|------|--------|--------|
| `okta.lookup_user` | FAIL (live) | `GET /api/v1/users/bob.departing@example.com` on the real org returned HTTP 404, `E0000007 Not found` |
| `okta.clear_sessions`, `okta.suspend` | SKIPPED | dependent on the failed lookup |
| Google Workspace and 1Password steps | OK (mock) | as in the demo |
| `alert.send_summary` | OK (live) | `POST` to the Slack webhook returned 200 `ok`; ~0.5 s round-trip versus the mock's ~1 ms |

Run time 1.65 s; exit code 1.

Evidence:

* [`2026-10-28-slack-alert-terminal.png`](evidence/2026-10-28-slack-alert-terminal.png)
  - terminal output; note the absence of a `[slack-mock]` transport line and
  the 0.5 s gap before "slack alert delivered".
* [`2026-10-28-slack-alert-channel.webp`](evidence/2026-10-28-slack-alert-channel.webp)
  - the Block Kit message as rendered in `#all-mal-lifecycle-automation`:
  header, user / workflow / started / duration fields, outcome line, every
  action with a tick, cross or skip marker, a separate *Failures* section
  quoting the Okta error body, and the footer `modes: okta=live, ...,
  alerts=live`.

Observed behaviour worth noting:

* The failure path is the one an on-call engineer actually sees: the Okta
  error body is quoted verbatim, and the dependent skips are labelled with
  the step that caused them.
* The webhook URL never appears in logs or output; it lives only in `.env`.
