# Five-day maintenance log

> Mock accounts (no credentials were supplied). Every entry is written on the real date the event happened; earlier days are never rewritten. Scripted mock-operator actions are labelled.

## Day 1 - 2026-11-23

| Time (UTC) | Account | Event | Detail | Actor |
|---|---|---|---|---|
| 06:54 | LI-001 | `account_onboarded` | Added to inventory. Admin of company page; primary LinkedIn regression account | qa-architect |
| 06:54 | LI-002 | `account_onboarded` | Added to inventory. Token issued earlier than others - near expiry at start of window | qa-architect |
| 06:54 | LI-003 | `account_onboarded` | Added to inventory. Member profile that administers one org page | qa-architect |
| 06:54 | FB-001 | `account_onboarded` | Added to inventory. Connected via admin profile; page token is non-expiring, user token 60d | qa-architect |
| 06:54 | FB-002 | `account_onboarded` | Added to inventory. | qa-architect |
| 06:54 | FB-003 | `account_onboarded` | Added to inventory. Admin profile that manages two Pages (multi-asset case) | qa-architect |
| 06:54 | IG-001 | `account_onboarded` | Added to inventory. Business account, Instagram Login flow | qa-architect |
| 06:54 | IG-002 | `account_onboarded` | Added to inventory. Creator account - personal IG accounts cannot be connected via API; Spikerz onboarding skipped at connect time | qa-architect |
| 06:54 | IG-003 | `account_onboarded` | Added to inventory. Linked to FB Page Two (Facebook Login flow) | qa-architect |
| 06:54 | TT-001 | `account_onboarded` | Added to inventory. | qa-architect |
| 06:54 | TT-002 | `account_onboarded` | Added to inventory. | qa-architect |
| 06:54 | TT-003 | `account_onboarded` | Added to inventory. First connection attempt failed (consent screen closed); succeeded on retry during setup | qa-architect |
| 06:54 | XX-001 | `account_onboarded` | Added to inventory. | qa-architect |
| 06:54 | XX-002 | `account_onboarded` | Added to inventory. Shares API rate-limit bucket with XX-001 | qa-architect |
| 06:54 | XX-003 | `account_onboarded` | Added to inventory. | qa-architect |
| 06:54 | IG-002 | `setup_incomplete` | Spikerz onboarding never completed for this account (no setup steps recorded): protection features not configured | healthcheck-bot |
| 06:54 | XX-002 | `transient_failure` | Recovered after 3 attempts (attempt 2: HTTP 429 Too Many Requests (simulated)) | healthcheck-bot |
| 06:54 | - | `health_check_run` | Daily health check dd821d9003d4: 15 accounts, 0 error/critical, 1 warning | healthcheck-bot |
| 06:54 | XX-002 | `corrective_action` | Reviewed 429 retries; no action needed. Spread X checks to reduce shared rate-limit pressure. [scripted mock operator] | mock-operator |
| 06:54 | IG-002 | `corrective_action` | Connected but Spikerz onboarding was skipped: completed setup steps (backup, comment and DM filters) in the Spikerz UI. [scripted mock operator] | mock-operator |
| 20:49 | IG-002 | `setup_incomplete` | Spikerz onboarding never completed for this account (no setup steps recorded): protection features not configured | healthcheck-bot |
| 20:49 | XX-002 | `transient_failure` | Recovered after 3 attempts (attempt 2: HTTP 429 Too Many Requests (simulated)) | healthcheck-bot |
| 20:49 | - | `health_check_run` | Daily health check b32ff28ee6cb: 15 accounts, 0 error/critical, 1 warning | healthcheck-bot |
| 20:49 | XX-002 | `corrective_action` | Reviewed 429 retries; no action needed. Spread X checks to reduce shared rate-limit pressure. [scripted mock operator] | mock-operator |
| 20:49 | IG-002 | `corrective_action` | Connected but Spikerz onboarding was skipped: completed setup steps (backup, comment and DM filters) in the Spikerz UI. [scripted mock operator] | mock-operator |

## Day 2 - 2026-11-24

| Time (UTC) | Account | Event | Detail | Actor |
|---|---|---|---|---|
| 19:54 | FB-001 | `issue_opened` | QA-001 [warning] Reported as connected, but the stored token expired on 2026-10-25: connection state and token metadata disagree | healthcheck-bot |
| 19:54 | IG-003 | `permission_change` | permissions: removed ['instagram_business_manage_comments']; added - | healthcheck-bot |
| 19:54 | IG-003 | `issue_opened` | QA-002 [error] Missing required permissions: instagram_business_manage_comments | healthcheck-bot |
| 19:54 | TT-003 | `connection_change` | connection_status: connected -> expired | healthcheck-bot |
| 19:54 | TT-003 | `issue_opened` | QA-003 [error] Token/session expired - Spikerz can no longer act on this account | healthcheck-bot |
| 19:54 | - | `health_check_run` | Daily health check 3ce0d93ee77d: 15 accounts, 2 error/critical, 1 warning | healthcheck-bot |
| 19:54 | TT-003 | `corrective_action` | Reconnected TikTok via Spikerz social-connect (owner present); consent granted for user.info.basic, video.list. [scripted mock operator] | mock-operator |
| 19:54 | IG-003 | `corrective_action` | Messaged owner: permission unticked while auditing apps. Reconnect scheduled for day 3. [scripted mock operator] | mock-operator |
| 19:54 | FB-001 | `corrective_action` | Connection flag and token record disagree: re-authorized the Page via the Spikerz UI to force a fresh token. [scripted mock operator] | mock-operator |

_3 monitoring day(s) still to run; the daily job appends them here._
