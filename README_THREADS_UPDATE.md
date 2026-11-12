# Newark Event Desk — Threads Connection update

**Prepared November 12, 2026**. This is a targeted extension of the existing app, not a replacement. It adds an admin-only Threads authorization workflow, keeps the existing Scan for Events function, and does not publish to Threads or Reddit. It does not change the Instagram connector yet.

## Before uploading

- You already exported your SQLite database and GitHub ZIP. Keep those private and safe.
- Do **not** add any Meta credentials, API tokens, `.env`, or `.sqlite3` database files to your public GitHub repository.
- Your Railway trial is expiring. Do not deploy until you confirm continued hosting on Railway Free (or another host). Meta App Review needs the app to stay reachable.

## GitHub — three files

At the root of `cupcakesriots/newarkevents`:

1. Replace existing **`dashboard.py`** with the file in this update. Keep its name exactly `dashboard.py`.
2. Replace existing **`social.py`** with the file in this update. Keep its name exactly `social.py`.
3. Add **`threads_connection.py`** as a new file.

Nothing else needs to be replaced. The `tests/test_threads_oauth.py` file is optional to add for future automated testing.

Do not accidentally upload `dashboard 2.py` or `social 2.py` as separate files; those duplicates will not update the code that Railway runs.

## Railway — private Variables for `newarkevents`

Add these exact environment variable **names** (values must be kept secret):

| Name | Value |
|---|---|
| `THREADS_APP_ID` | The **Threads App ID** from the Threads use case's settings in Meta for Developers; not the general Facebook App ID |
| `THREADS_APP_SECRET` | Matching **Threads App Secret** from Meta for Developers |
| `THREADS_REDIRECT_URI` | `https://newarkevents-production.up.railway.app/oauth/threads/callback` |
| `THREADS_EXPECTED_USERNAME` | `transformthehustle` (recommended; optional safety check) |

Continue keeping `DASHBOARD_HOST`, `DASHBOARD_PASSWORD`, `NEWARK_DB`, and `PUBLIC_BROWSE_ENABLED` exactly as they are. You can leave `THREADS_ACCESS_TOKEN` temporarily; the new authenticated connection takes precedence. After successful connection and testing, it can be removed if you choose. No token value should ever be pasted into GitHub.

## Meta for Developers — redirect URL

Open **Newark Event Desk → Use cases → Access the Threads API → Settings** and add this to **Redirect Callback URLs / Valid OAuth Redirect URIs** (name depends on the dashboard):

`https://newarkevents-production.up.railway.app/oauth/threads/callback`

The URI in Meta and `THREADS_REDIRECT_URI` in Railway must match **exactly**, including HTTPS and path. Save the setting. Make sure the Threads account you will connect has already accepted its tester invitation (as you did earlier).

## Test in your browser

After a successful deployment:

1. Log in to your admin dashboard at `https://newarkevents-production.up.railway.app`.
2. Select **Threads Connection** in the admin navigation.
3. Click **Connect Threads with Meta**. Complete the actual authorization screen using `transformthehustle`.
4. You should return to Event Desk and see **Connected as @transformthehustle**. The token itself never appears in the page.
5. On the same page, test **Newark events** or a distinctive phrase from an *existing public Threads post on your account* using **Search Threads & check for leads**.
6. Open **Social Leads** to review any qualifying discoveries, or use **Scan for Events** to run the regular multi-source collector.

Before Meta approves `threads_keyword_search` for advanced access, searches may return only posts belonging to the connected account. This is a **real restriction**, not a bug in the app. Do not present empty results as evidence of public Newark-wide search.

## What to record for Meta App Review

Record a real demonstration of:

1. Admin logs into Newark Event Desk.
2. Opens **Threads Connection** and clicks **Connect Threads with Meta**.
3. The official Meta/Threads authorization dialog and return to Event Desk.
4. The connected username and the **Search Threads & check for leads** action.
5. A genuine returned matching Threads post (if available), then the **Social Leads** review interface and a manual review decision.

Capture the actual workflow using macOS **Shift + Command + 5**. Do not expose admin passwords, API tokens, or app secrets. Meta still requires its test-call counter and review steps to complete; a recording alone does not grant permission.

## Security and maintenance notes

- Authorization state is one-use, expires after 10 minutes, and is checked in the SQLite database.
- OAuth code exchange happens server-side. The Threads App Secret and access tokens are not rendered on the page.
- Connected Threads token is stored **in the same SQLite database** as event data. This means future backups of that database contain a live credential; treat them as confidential.
- The scanner prefers the connected OAuth token; it can refresh a long-lived token near expiration. Reconnecting is possible if it expires or is revoked.
- The `Disconnect Threads` button clears the saved token and prevents the older `THREADS_ACCESS_TOKEN` environment variable from silently reactivating the scanner.
- The dashboard remains protected by the existing `DASHBOARD_PASSWORD` HTTP Basic authentication. The OAuth callback alone is intentionally public but accepts only a valid single-use state.
- The login, token exchange, and actual Meta data collection have **not been tested against live Meta**, since no credentials are included here. Offline tests pass, and the local website/OAuth-start routes were smoke-tested.

## Troubleshooting

| Symptom | Check |
|---|---|
| Missing settings message | All 3 required Railway variables have been deployed and the service restarted |
| Meta rejects OAuth redirect | Exact match of the HTTPS callback URI in Meta and Railway |
| Meta says app invalid | Verify you used the **Threads** App ID and App Secret, not the regular Meta App credentials |
| Connects as wrong account | Confirm you are signed in to the correct Threads account; the expected username check is deliberate |
| Empty keyword results | Check whether the connected account has a post containing the search phrase; broader public search awaits App Review |
| 403 Forbidden on keyword search | Permission still restricted or token lacks `threads_keyword_search`; inspect the token with Meta's Access Token Debugger (never share token publicly) |
| Meta testing counter still 0/1 | Meta says the test-call status may take up to 24 hours to update; verify app/token alignment |

This change does not add new Instagram OAuth functionality. Keep Instagram as the next, separate step after the Threads authorization and App Review demonstration are working.
