# Assumptions and clarifications

This file records every judgement call behind the build: where the brief left room for interpretation, where two requirements pull against each other, and where platform realities shape what a QA tool can do. Each item gives the brief's wording, the constraint, and the decision taken, so every choice can be traced and challenged.

---

## Part 1: Clarifications on the brief

### M-01. The 72-hour deadline and five calendar days of monitoring

The brief says to submit within 72 hours. It also says the daily checks must cover five separate calendar days, starting after setup and connection work is finished. Setup takes at least part of day 0, so the earliest possible finish is day 5 (setup, then days 1 to 5). That is about 120 hours or more, so the two requirements can't both be met with genuine data.

What I did:
- **Monitoring is forward-only.** Day 1 is the real date the first check ran (**2026-11-23**). Each later daily run appends the next day, and earlier days are never rewritten. Re-running on the same day only replaces that day.
- The tool **cannot** backdate or post-date a check: there is no `--as-of` option, and every check is recorded against today's UTC date (a test enforces this). I didn't pre-generate future days or invent logs, because that would be falsified QA evidence.
- What happens on each day comes from a deterministic mock scenario (`data/scenario.json`) written in *monitoring days*, not dates. Day N's state is only produced when Day N's check actually runs.
- The dashboard shows progress (for example "1/5 days") and marks future days as **pending**. The maintenance log ends with "N monitoring day(s) still to run".
- Days 2 to 5 run on a schedule: the GitHub Actions `schedule:` job commits each new day back to the repo, so the commit history itself proves the dates. `scripts/daily_check.sh` from cron is the local alternative.
- **Recommendation:** submit at the 72-hour mark with the days completed so far (Days 1 to 3 or 4), and state that the remaining days are being appended automatically. Alternatively, agree a short extension with the hiring team.

### M-02. "Create or manage 15 social-media test accounts" runs into platform policy

Most platforms forbid fake, duplicate or bulk-created accounts:
- **LinkedIn**: one profile per real person. Fake profiles get banned. There is no general-purpose test-user facility.
- **Meta**: one personal account per person. The sanctioned route is *test users* and *test Pages* on a developer app in Development mode.
- **X**: multiple accounts are allowed, but inauthentic or automated behaviour isn't. API access to many features needs a paid tier.
- **TikTok**: offers a sandbox for developer apps. Production accounts need real identities.

So a "controlled set of 15 test accounts" can only be built from (a) real accounts owned by named employees, or (b) each platform's official sandbox or test-user features. Bulk-creating accounts for QA would break the "work within platform policies" requirement in the same brief. **This is why I used mock accounts** (A-01). The inventory schema already supports real ones (`environment=sandbox|production_test`, `credential_ref=env:...`).

### M-03. "Facebook profile or Page": a profile isn't a connectable asset

The Meta Graph API gives third-party apps almost no access to **personal profiles**. Comment moderation, which is what a protection tool like Spikerz needs, works on **Pages**. A personal profile is only the *admin identity* that authorizes access to Pages. In the inventory:
- `FB-001` and `FB-002` are Pages (`account_type=page`).
- `FB-003` is an admin *profile* (`account_type=personal_profile`) whose **connected assets are two Pages**. This covers the multi-asset case: one admin identity, several Pages. (Detection of a lost asset is demonstrated on LI-003, Day 3.)

### M-04. Instagram: personal accounts can't be connected

The Instagram APIs only work with **Professional** accounts (Business or Creator). A personal Instagram account will fail at connection time. All three IG accounts are `business` or `creator`. `IG-003` uses the Facebook-Login flow (linked to a Page). The others use the Instagram-Login flow.

### M-05. "Token/session validity": QA can only check tokens it holds

Spikerz holds the OAuth tokens it obtains when a user connects an account. A separate QA tool **can't see those tokens**, and shouldn't. So "is this account still connected *in Spikerz*?" can only be observed in one of two ways:
1. **In the Spikerz UI** (manual or Playwright-driven), or
2. **Through a Spikerz internal API**, for example `GET /internal/connections/{account}`, if engineering provides one.

A QA-owned developer app holding its *own* tokens can check **platform-side** health: whether the account is restricted, which assets are visible, and which scopes are granted. But a valid QA token doesn't prove that Spikerz's token is valid.

What I did: the provider interface (`spikerz_qa/providers`) keeps this concern separate. `live.py` implements platform-side checks. The **recommended next step** is a `SpikerzProvider` that calls an internal Spikerz connection-status endpoint, or a Playwright check of the Connected Accounts page. That needs Spikerz access, which wasn't provided (M-07).

### M-06. "Automatic connection where authorized" has narrow limits

OAuth consent is built to need a human: login, 2FA, CAPTCHA and the consent screen. Scripting platform login pages (for example Selenium on facebook.com/login) is against the platforms' terms. It also reliably triggers security locks; Day 4 of the scenario (XX-003 locked) shows this exact failure mode. Automatic connection is only legitimate through:
- Meta test users and test Pages on a Development-mode app,
- TikTok's developer sandbox,
- long-lived or refresh tokens issued once by a human and then refreshed through the API.

What I did: **manual connection through the Spikerz UI** is the primary workflow (`docs/connection-workflow.md`). Automation is limited to verifying the connection *after* it's made, plus refreshing tokens through official endpoints.

### M-07. No Spikerz environment or credentials were provided

The brief asks for "screenshots or screen recording of the Spikerz UI connection process", but didn't include a Spikerz workspace, test tenant or login. I didn't sign up for a Spikerz account with personal details to get around this, and I didn't fabricate UI screenshots.

What I did: `docs/connection-workflow.md` is a step-by-step runbook with an evidence checklist per account. `evidence/` has the folder structure and naming convention, ready to fill in. The UI steps are written generically (A-03) and need a 10-minute check against the real UI once access is granted. Screenshots of the **tool itself** (dashboard, reports) are real.

### M-08. Five days of real monitoring will probably show nothing

Tokens last 60 days or more, and restrictions are rare. A real five-day window on 15 healthy accounts will most likely produce five identical green reports. That proves the cron job ran, not that detection works. A test architect has to **inject faults** to show that each failure class is detected, reported and resolved. The scenario covers each class at least once:

| Failure class | Account / day | Detected as | Resolved |
|---|---|---|---|
| Transient rate limit, retry succeeds | XX-002 / D1 | info + `transient_failure` log | same run |
| Connected, but Spikerz onboarding never completed | IG-002 / D1 | warning + `setup_incomplete` log | D2 |
| Token / session expired | TT-003 / D2 | error, issue opened | D3 (reconnected) |
| Permission revoked by the owner | IG-003 / D2 | error, issue opened | D4 (reconnected with full consent) |
| Connection flag says "connected" but the token record has lapsed | FB-001 / D2 | warning, `token_metadata` issue | D3 (re-authorized) |
| Token close to expiry | LI-002 / D3 | warning + alert, proactive re-auth | D4 (`token_refresh`) |
| Asset lost (admin role removed) | LI-003 / D3 | warning, `assets_change` | D4 |
| Account/Page deactivated on the platform | FB-002 / D3 | error, issue opened | D4 (republished) |
| Account locked by the platform | XX-003 / D4 | critical, issue opened | D5 |
| Unlock revokes tokens (knock-on failure) | XX-003 / D5 | error, issue opened | **left open** with next action |
| Retries exhausted, outcome unknown | LI-002 / D5 | warning, `check_failure` issue; inventory **not** overwritten | **left open** |

Three of these classes (onboarding incomplete, connection flag and token record out of sync, account deactivated as distinct from restricted) go beyond "is the token valid?". They reflect how a protection product actually degrades: an account can look connected while protection isn't working.

### M-09. "Permission status" isn't readable on every platform

Only some platforms let you introspect the scopes granted to an existing token:
- **Meta**: `debug_token` returns scopes.
- **LinkedIn**: token introspection, for approved apps.
- **TikTok and X**: return `scope` only at token exchange or refresh time.

The data model keeps `permissions = None` ("unknown") separate from `[]` ("none granted"), so the tool never reports a false "missing permissions" error when the platform simply doesn't say (`tests: test_unknown_permissions_not_flagged`).

### M-10. Feature availability per platform

- **X**: reading users and moderating replies needs a paid API tier. Free tier access is very limited.
- **TikTok**: the Display API (`user.info.basic`, `video.list`) doesn't expose comment moderation. Spikerz may use a partner or business API whose scopes I can't see. The scopes listed are a minimum for "is this account reachable".

---

## Part 2: Assumptions

| ID | Assumption | Why / impact |
|---|---|---|
| A-01 | **All 15 accounts are mock records** (`environment=mock`, `credential_ref=mock:...`). | M-02 and M-07. Gives a repeatable, policy-safe, CI-runnable demonstration. Switching to real accounts means editing the inventory, not the code. |
| A-02 | The **live adapters** (`providers/live.py`) follow each platform's public API docs and are unit-tested only with stubbed HTTP. They have **not** been run against live accounts. | No authorized tokens were available. Endpoint versions (for example Graph `v21.0`) may need updating when this goes live. |
| A-03 | The Spikerz connection flow is assumed to be the usual SaaS OAuth pattern: *Accounts / Connect account / choose platform / platform login and consent / redirect back / account listed as Connected*. | I had no access to the real UI. The runbook marks each UI step to be checked. |
| A-04 | Setup, onboarding and **Day 1 on 2026-11-23** (monitoring started once setup was done that morning). Days 2 to 5 are **2026-11-24 to 2026-11-27**, each recorded only when it actually runs. Day numbers are calendar-based, so a missed day shows as a gap rather than shifting later days. | Forward-only and honest. A gap in the record is visible instead of hidden. |
| A-05 | One check per account per day; the schedule runs at 09:00 UTC, and timestamps are the real time of the run. Re-running on the same day **replaces only today's** result (`UNIQUE(run_date, account_id)`). | Makes the daily job idempotent, so a cron retry can't double-count, and past days can't be touched. |
| A-06 | Severity: `critical` = account locked or suspended. `error` = Spikerz can't operate (expired, disconnected, missing required scope, restricted, deactivated). `warning` = degraded or unknown (token expires within 7 days, connection flag and token record disagree, Spikerz onboarding incomplete, lost asset, check couldn't finish). `info` = notable but healthy (retry recovered, new asset or scope). | Matches how a QA lead would triage. Restriction, connection, permission, token-record and check-failure findings at warning level or above open a tracked issue; the rest alert and log. |
| A-07 | The required scopes per platform (`models.REQUIRED_PERMISSIONS`) are my best reading of what a comment-moderation and protection product needs. | Spikerz's real consent screen is the source of truth. It's one dict to update. |
| A-08 | Token lifetimes are modelled on the **effective session horizon**: Meta long-lived user token 60 days (Page tokens derived from it don't expire), Instagram 60 days, LinkedIn 60 days, TikTok refresh token 365 days (access token 24 hours), X refresh token with `offline.access` (access token 2 hours; 180 days modelled). | Short-lived access tokens are refreshed routinely. What matters for QA is whether the session can be renewed. |
| A-09 | Account IDs follow `<2-letter platform>-<3 digits>`. X uses `XX` because `X` alone is one letter. | Readable and sortable. Validated by regex. |
| A-10 | The health check is the **source of truth** for status fields (`status`, `connection_status`, `connected_assets`, `permissions`). Operators own the metadata (`owner_team`, `notes`, `environment`, `credential_ref`). If a check can't finish, it **does not** overwrite the inventory. | Avoids recording a guess as fact. |
| A-11 | It's a single-operator CLI with no login. The audit trail uses `--actor` or `SPIKERZ_QA_ACTOR`, falling back to the OS user. | Enough for a take-home. A shared deployment would take identity from SSO (see REFLECTION). |
| A-12 | Accounts are never hard-deleted. `retire` is a soft delete that keeps history and prints a cleanup checklist. | QA evidence has to stay traceable after an account is gone. |
| A-13 | Mock-operator corrective actions are scripted in `scenario.json` (`operator_actions`). They are logged on the day of detection with the actor `mock-operator` and tagged `[scripted mock operator]`. Real actions are logged with `spikerz-qa log`. | Shows the intended triage loop (alert, action, verification next day) without passing scripted text off as human work. |
| A-14 | I chose Option A (Python), using the **standard library only** (sqlite3, argparse, urllib, unittest). | No install step, so a reviewer can run it in under a minute. FastAPI or APScheduler would add dependencies without adding substance at this size. |
