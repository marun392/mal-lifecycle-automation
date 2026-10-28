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

### Offboarding: pending

To be recorded after running:

```
python -m mal_lifecycle --json offboard-okta.json offboard --email marun392+testhire@gmail.com
```

Expected: `okta.lookup_user`, `okta.clear_sessions` and `okta.suspend` return
OK and the console status changes to *Suspended*.

## Google Workspace

Not yet run against a live domain.

## 1Password

Not yet run against a live SCIM bridge.

## Jamf Pro

Mocked by design; no live run planned for this submission.

## Slack / email alerts

Not yet run against a live webhook.
