# Account onboarding and Spikerz UI connection workflow

> Spikerz UI steps are written to the standard OAuth "Connect account" pattern (ASSUMPTIONS A-03).
> Steps marked **[verify]** must be confirmed against the real Spikerz UI once a QA workspace is provided (M-07).

## 1. Lifecycle

```
 provision ──► register in inventory ──► connect in Spikerz UI ──► verify ──► monitor daily
 (owner)       (spikerz-qa import/add)   (manual OAuth, owner)     (check)    (cron / CI)
                                                                     │
                     retire ◄── unrecoverable ◄── triage issue ◄─────┘ (error/critical)
```

## 2. Pre-requisites (per account)

| Check | Why |
|---|---|
| Account is owned by a named person/team (`owner_team`) | Accountability; platforms require real owners (M-02) |
| 2FA enabled with a shared QA authenticator / vault entry | Avoids lock-outs; never SMS on a personal phone |
| Facebook: profile is **Admin** of the target Page(s) | Pages are the connectable asset (M-03) |
| Instagram: account is **Business or Creator** | Personal IG cannot be connected (M-04) |
| LinkedIn: member has **Super admin** on the company page | Required for org-level scopes |
| TikTok: Business/Creator account | — |
| X: account email/phone verified | Unverified accounts lock quickly (see XX-003) |
| Login from a stable, residential/office IP — never a datacenter/VPN | Datacenter logins trigger locks |

## 3. Register in the inventory

```bash
python -m spikerz_qa add TT-004 --platform tiktok --account-type business \
  --environment production_test --team qa-core \
  --credential-ref env:SPIKERZ_QA_TT_004_TOKEN --handle @spikerz.qa.tt04
```
Status starts as `connection_status=pending`.

## 4. Connect in the Spikerz UI (manual, owner present)

Common steps:

1. Log into the Spikerz QA workspace. **[verify]**
2. Go to **Accounts / Connected accounts → Connect account**. **[verify]**
3. Choose the platform. You are redirected to the platform's OAuth page.
4. Log in **as the test account** (owner enters 2FA).
5. On the consent screen, **leave every requested permission ticked**. Screenshot the consent screen.
6. Select the asset(s) where asked (Pages, IG account, LinkedIn organization).
7. You are redirected back to Spikerz. **Verify the confirmation step shows the intended handle.** Picking the wrong account at this point (the owner is logged into several) is the most common silent onboarding mistake. Screenshot.
8. **Finish Spikerz onboarding** (setup/security questions) rather than skipping it. A connected account with no completed setup isn't actually protected; the health check flags it as `setup_incomplete` (IG-002, Day 1).
9. The account shows **Connected**. Screenshot.
10. Mark connected and record evidence:
   ```bash
   python -m spikerz_qa update TT-004 --set connection_status=connected \
     --set "connected_assets=tiktok_user:<id>" --reason "Connected via Spikerz UI"
   python -m spikerz_qa check --account TT-004 --provider live
   ```

Platform-specific notes:

| Platform | Step 6 detail | Common failure |
|---|---|---|
| Facebook | "Choose the Pages you want Spikerz to access" — select **all** QA Pages | Admin role missing → page not listed |
| Instagram (FB Login) | Select the FB Page linked to the IG Business account | IG not linked to a Page → no IG asset |
| Instagram (IG Login) | Approve `instagram_business_*` scopes | Personal IG → "account not eligible" |
| LinkedIn | Select the organization page | Not super-admin → org not listed |
| TikTok | Approve scopes on TikTok consent | Closing the consent tab → silent failure (TT-003 setup note) |
| X | "Authorize app" | Locked account → 403 on callback |

## 5. Evidence checklist (per account)

Save under `evidence/<ACCOUNT_ID>/` using `YYYY-MM-DD_<step>.png`:

- [ ] `01_connect_button.png` — Spikerz Connect account dialog with platform chosen
- [ ] `02_consent.png` — platform consent screen showing requested scopes
- [ ] `03_asset_select.png` — asset selection (if applicable)
- [ ] `04_confirm_handle.png`: confirmation step showing the expected handle
- [ ] `05_connected.png`: Spikerz showing the account as Connected
- [ ] `06_healthcheck.txt`: output of `spikerz-qa check --account <ID>`

Redact: email addresses, phone numbers, real names of employees, any token in URLs (`code=`, `access_token=`). Unredacted originals go in `evidence/<ID>/raw/` (git-ignored).

## 6. Reconnection (corrective action)

Used for `connection`, `permissions` and `token_metadata` issues (in the scenario: TT-003 on Day 2, IG-003 on Day 2, FB-001 on Day 2, XX-003 on Day 5):

1. Spikerz UI → account → **Reconnect** (or remove and connect again). **[verify]**
2. Repeat steps 4–9 above.
3. `python -m spikerz_qa log "Reconnected via Spikerz UI" --account <ID>`
4. Re-run `check --account <ID>`; the issue auto-closes when the condition clears.

## 7. Restriction / lock handling (do not automate)

1. **Stop** all automated checks that log in for this account (API token checks are fine).
2. Owner completes the platform's challenge manually from a trusted network.
3. Expect tokens to be revoked after unlock (XX-003, Day 5) → reconnect (section 6).
4. If not recovered in 72h → `spikerz-qa retire <ID> --reason ...` and provision a replacement.

## 8. Account connection record (this submission)

All 15 accounts are mock (A-01); the table shows the evidence that **would** be produced. Columns become links once real screenshots exist.

| ID | Platform | Asset(s) | Connected (setup) | Evidence folder |
|---|---|---|---|---|
| LI-001 | LinkedIn | org:qa-1001 | 2026-11-23 (mock) | `evidence/LI-001/` |
| LI-002 | LinkedIn | org:qa-1002 | 2026-11-23 (mock) | `evidence/LI-002/` |
| LI-003 | LinkedIn | org:qa-1003 | 2026-11-23 (mock) | `evidence/LI-003/` |
| FB-001 | Facebook | page:qa-2001 | 2026-11-23 (mock) | `evidence/FB-001/` |
| FB-002 | Facebook | page:qa-2002 | 2026-11-23 (mock) | `evidence/FB-002/` |
| FB-003 | Facebook | page:qa-2003, page:qa-2004 | 2026-11-23 (mock) | `evidence/FB-003/` |
| IG-001 | Instagram | ig_account:qa-3001 | 2026-11-23 (mock) | `evidence/IG-001/` |
| IG-002 | Instagram | ig_account:qa-3002 | 2026-11-23 (mock) | `evidence/IG-002/` |
| IG-003 | Instagram | ig_account:qa-3003 (+ page:qa-2002) | 2026-11-23 (mock) | `evidence/IG-003/` |
| TT-001 | TikTok | tiktok_user:qa-4001 | 2026-11-23 (mock) | `evidence/TT-001/` |
| TT-002 | TikTok | tiktok_user:qa-4002 | 2026-11-23 (mock) | `evidence/TT-002/` |
| TT-003 | TikTok | tiktok_user:qa-4003 | 2026-11-23 (mock, 2nd attempt) | `evidence/TT-003/` |
| XX-001 | X | x_user:qa-5001 | 2026-11-23 (mock) | `evidence/XX-001/` |
| XX-002 | X | x_user:qa-5002 | 2026-11-23 (mock) | `evidence/XX-002/` |
| XX-003 | X | x_user:qa-5003 | 2026-11-23 (mock) | `evidence/XX-003/` |
