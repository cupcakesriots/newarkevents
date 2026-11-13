# Newark Event Desk — Threads OAuth HTTP 400 Fix

**November 12, 2026.** This is a targeted one-file replacement for the deployed Newark Event Desk version that shows `Meta API returned HTTP 400. Check the Threads app permissions and redirect URI.` immediately after clicking **Connect Threads with Meta**.

## What changed

- The Meta Threads long-lived token exchange now sends the required temporary token in the `access_token` request parameter, rather than only an `Authorization` header.
- Code exchange and profile lookup use the request parameters documented for the Threads API.
- The token refresh request uses the documented `access_token` parameter.
- A failed API exchange now reports its stage (authorization code exchange, long-lived token exchange, or profile lookup) **without printing Meta's response body, access tokens, app secret, or OAuth code**.

## GitHub upload

1. Download this ZIP and unzip it.
2. Open the root folder of the `cupcakesriots/newarkevents` GitHub repository.
3. Replace **only** `threads_connection.py` with the file in this ZIP (don't create `threads_connection 2.py`).
4. Commit to `main`, and wait for a successful Railway deployment.
5. On Newark Event Desk, open **Threads Connection → Connect Threads with Meta** again. A new click creates a fresh one-use OAuth state and authorization code; don't reuse the old callback URL.

## If it still says HTTP 400

The updated message will identify **which step** failed. Share only that message (and a screenshot with any URL query parameters/authorization tokens obscured). A failure during code exchange can be caused by an incorrect **Threads App ID / Threads App Secret** pairing or an exact-match redirect URI issue. A failure during long-lived exchange may relate to the token exchange or Meta API availability. Do not post secrets or access tokens in ChatGPT or GitHub.

## Existing configuration

Keep the four Railway variables unchanged unless you discover a mismatch: `THREADS_APP_ID`, `THREADS_APP_SECRET`, `THREADS_REDIRECT_URI`, `THREADS_EXPECTED_USERNAME`. Verify the Threads-specific credentials from **Meta for Developers → Access the Threads API → Settings**; these aren't necessarily the same as the general Meta App ID/secret. Keep the Redirect, Uninstall, and Delete callback URLs in Meta as previously saved.

## Testing and limits

All 22 local/offline tests pass. **No live Meta API request was made**. A successful OAuth connection will not by itself enable search of everyone's Threads posts: Meta's advanced permission review is still required for broader keyword search.
