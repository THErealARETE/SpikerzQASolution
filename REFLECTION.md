# Reflection

## What did you build?

`spikerz-qa` is a Python command-line tool (Option A) that uses only the standard library. It manages a 15-account inventory across LinkedIn, Facebook, Instagram, TikTok and X:

- **Inventory**: SQLite storage with create, update, view, filter, import (CSV/JSON) and export. Every field is validated. `credential_ref` only ever holds a *reference* (`env:`, `vault:`, `aws-sm:`, `mock:`), and raw secrets are rejected on input.
- **Daily health check**: providers chosen per account through a router (`mock` is a deterministic scenario keyed by monitoring day; `live` uses official read-only APIs). Includes bounded exponential-backoff retry, and a pure `evaluate()` that turns raw platform state into severity-ranked findings: restriction (including deactivated), connection, missing or changed permissions, asset changes, token expiry within 7 days, connection flag disagreeing with the token record, incomplete Spikerz onboarding, and platform errors.
- **Issue tracking**: issues (QA-xxx) open automatically with repro steps, evidence, likely root cause and next action. They close automatically when the condition stops reproducing, and are deduplicated per account and category.
- **Logs and audit**: an automatic five-day maintenance log covering connection, status, permission and asset changes, token refreshes, transient failures, corrective actions and resolutions. There is also a per-field audit trail recording who changed what and when.
- **Reports**: daily JSON, CSV and HTML, plus a dashboard showing health by platform and day and by account and day. Alerts go to `alerts.jsonl` and, optionally, a Slack or Teams webhook.
- **Optional extras covered**: retry logic, alerts, dashboard, audit trail, retirement workflow, and CI (tests on 3.10 and 3.12, plus a scheduled daily job that commits each new day back to the repo).
- **Tests**: 44 unit and integration tests. They include a five-day replay in an in-memory database that checks every expected outcome and issue lifecycle, and tests that enforce forward-only monitoring: no backdating option, and earlier days left unchanged when a new day runs.

## What challenges did you face?

1. **The brief contradicts itself.** Five calendar days of monitoring can't fit inside a 72-hour deadline. I made monitoring forward-only: Day 1 is today, each scheduled run appends the next day, and nothing is pre-generated or backdated. I'd rather submit 3 or 4 honest days than 5 invented ones (ASSUMPTIONS M-01).
2. **Platform policy against the request.** Creating 15 throwaway social accounts breaks platform terms, which the same brief says to respect. Mock accounts were the only approach that is both policy-safe and repeatable (M-02).
3. **Observing Spikerz's own connection state.** A QA tool can't see the tokens Spikerz holds. I separated platform-side health (built) from Spikerz-side connection status (needs an internal endpoint or UI automation) and documented that gap rather than hiding it (M-05).
4. **No Spikerz access.** I wrote the connection process as a runbook with an evidence checklist. I didn't fabricate screenshots (M-07).
5. **Uneven platform APIs.** Only some platforms expose granted scopes. I modelled "unknown" separately from "none" so the tool doesn't raise false alarms (M-09).
6. **Evidence integrity and self-review.** An early prototype replayed all five days in one run. I rejected it because a replay records dates that haven't happened yet, which no QA log should do, and made monitoring forward-only with a test that enforces it. Self-review also caught three defects before submission. The first run logged spurious `permission_change` events on Day 1 because list order differed but the content didn't. It also missed logging asset changes. Both were fixed, and tests pin the behaviour. Reviewing my own dashboard screenshot also caught Instagram, TikTok and X handles losing their leading `@`, because a leading `@` was doubling as a layout marker. I replaced the marker with a type and added a regression test.

## What would you improve with more time?

- A **`SpikerzProvider`** that reads connection status from Spikerz itself, through an internal API or a Playwright check of the Connected Accounts page. Its result would be compared with platform-side health to catch cases where Spikerz says "connected" while the platform says "revoked".
- Run the **live adapters against real sandbox accounts**: Meta test users and Pages, and the TikTok sandbox. Record HTTP fixtures (VCR-style) so tests replay real payloads.
- A **Playwright-captured connection flow** (screenshots plus video) for every account, attached automatically to `evidence/`.
- A real **secrets backend** (Vault or AWS Secrets Manager resolvers), plus token refresh where platforms allow it (LinkedIn, TikTok and X refresh tokens).
- A small **FastAPI read-only dashboard** with SSO, so the audit "actor" comes from identity rather than an environment variable.
- **Trend alerts** such as "same account failed twice in 7 days", and suppression windows during platform outages, using public status pages.

## How would this scale from 15 accounts to 100+?

| Concern | At 15 | At 100+ |
|---|---|---|
| Storage | SQLite file | PostgreSQL, with the same schema plus indexes on `(run_date, platform)`, and partitioning of `health_checks` by month |
| Execution | Sequential loop | Fan out per account on a queue (Celery/RQ/SQS) with **per-platform concurrency and rate-limit budgets**: a token bucket per app and platform, because limits are per app as well as per account |
| Scheduling | cron / GitHub Actions | One orchestrator (Airflow, Temporal or a K8s CronJob) with check times spread across the day, so not all X accounts hit the API at 09:00 |
| Secrets | env vars | Vault or Secrets Manager with per-team paths, automatic rotation, and refresh tokens renewed by a separate job |
| Ownership | `owner_team` field | Team-based routing of alerts and issues (to Jira) and a weekly "inventory hygiene" report: stale owners, accounts not connected for N days, distribution per platform below minimum |
| Provisioning | Manual | A request-and-approval workflow. Account creation stays manual per platform policy, but registration, connection evidence and retirement are tracked as a checklist |
| Reporting | Static HTML | A dashboard (Grafana/Metabase) on Postgres. SLOs such as "≥ 95% of QA accounts connected" with burn-rate alerts |
| Test data integrity | One scenario | A scenario library per failure class, run in CI against every change to the health-check logic. Real-account checks are kept separate from fault-injection checks |
| Cost and policy | n/a | Budget for paid API tiers (X). Keep accounts low-activity and human-like. Never automate platform logins: at scale, lock-outs grow faster than headcount |

The code is already split along the right seams for this: provider, evaluator, store and reporters. Scaling mostly means changing infrastructure, not rewriting.
