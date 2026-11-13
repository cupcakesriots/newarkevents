"""Admin-only Threads OAuth connection for Newark Event Desk.

- Tokens never appear in URLs, templates, or logs.
- OAuth callback uses a one-time CSRF state stored in SQLite.
- The approved authorization is reused by the existing Scan for Events pipeline.
- No permission to publish, delete, or reply is requested.
"""
from __future__ import annotations

import base64
import hashlib
import json
import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

import requests

API = 'https://graph.threads.net'
AUTH = 'https://threads.net/oauth/authorize'
TIMEOUT = (10, 25)
SCOPES = 'threads_basic,threads_keyword_search'


class ThreadsConnectionError(Exception):
    """User-facing error without tokens or provider response bodies."""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS threads_oauth_states (
       state_hash TEXT PRIMARY KEY,
       created_at INTEGER NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS threads_connection (
       id INTEGER PRIMARY KEY CHECK(id=1),
       user_id TEXT NOT NULL DEFAULT '',
       username TEXT NOT NULL DEFAULT '',
       access_token TEXT NOT NULL DEFAULT '',
       obtained_at INTEGER NOT NULL DEFAULT 0,
       expires_at INTEGER NOT NULL DEFAULT 0,
       status TEXT NOT NULL DEFAULT 'connected'
    )''')
    conn.commit()


def settings() -> tuple[str, str, str]:
    app_id = os.getenv('THREADS_APP_ID', '').strip()
    app_secret = os.getenv('THREADS_APP_SECRET', '').strip()
    callback = os.getenv('THREADS_REDIRECT_URI', '').strip()
    parsed = urlparse(callback)
    if not all((app_id, app_secret, callback)):
        raise ThreadsConnectionError('Add THREADS_APP_ID, THREADS_APP_SECRET and THREADS_REDIRECT_URI in Railway Variables first.')
    if parsed.scheme != 'https' or not parsed.netloc or parsed.query or parsed.fragment or parsed.path != '/oauth/threads/callback':
        raise ThreadsConnectionError('THREADS_REDIRECT_URI must be a public HTTPS URL ending in /oauth/threads/callback.')
    return app_id, app_secret, callback


def start(conn: sqlite3.Connection) -> str:
    app_id, _, callback = settings()
    ensure_tables(conn)
    state = secrets.token_urlsafe(32)
    digest = hashlib.sha256(state.encode()).hexdigest()
    conn.execute('DELETE FROM threads_oauth_states WHERE created_at < ?', (int(time.time()) - 600,))
    conn.execute('INSERT INTO threads_oauth_states (state_hash, created_at) VALUES (?, ?)', (digest, int(time.time())))
    conn.commit()
    return AUTH + '?' + urlencode({
        'client_id': app_id,
        'redirect_uri': callback,
        'scope': SCOPES,
        'response_type': 'code',
        'state': state,
    })


def _consume_state(conn: sqlite3.Connection, state: str) -> None:
    if not state or len(state) > 200:
        raise ThreadsConnectionError('Missing or invalid authorization state. Restart the connection.')
    digest = hashlib.sha256(state.encode()).hexdigest()
    match = conn.execute('SELECT created_at FROM threads_oauth_states WHERE state_hash=?', (digest,)).fetchone()
    if not match or time.time() - int(match[0]) > 600:
        raise ThreadsConnectionError('Authorization expired or was already used. Restart the connection.')
    conn.execute('DELETE FROM threads_oauth_states WHERE state_hash=?', (digest,))
    conn.commit()


def _json_response(resp: requests.Response, step: str = 'request') -> dict:
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ThreadsConnectionError(f'Meta returned an unexpected response during {step}.') from exc
    if resp.status_code >= 400 or 'error' in payload:
        # Deliberately do not expose provider response bodies, which may contain secrets.
        raise ThreadsConnectionError(f'Meta returned HTTP {resp.status_code} during {step}. Check the Threads App ID/Secret and exact redirect URI if this is the code exchange.')
    return payload


def finish(conn: sqlite3.Connection, state: str, code: str) -> str:
    _consume_state(conn, state)
    if not code or len(code) > 4000:
        raise ThreadsConnectionError('No authorization code returned by Meta.')
    app_id, app_secret, callback = settings()
    try:
        short = _json_response(requests.post(API + '/oauth/access_token', params={
            'client_id': app_id, 'client_secret': app_secret,
            'code': code, 'grant_type': 'authorization_code', 'redirect_uri': callback,
        }, timeout=TIMEOUT), 'authorization code exchange')
        token = short.get('access_token', '')
        if not token:
            raise ThreadsConnectionError('Meta did not return an access token.')
        expires = int(time.time()) + int(short.get('expires_in', 3600))
        # Exchange the short-lived token for a long-lived token when available.
        long = _json_response(requests.get(API + '/access_token', params={
            'grant_type': 'th_exchange_token', 'client_secret': app_secret,
            'access_token': token,
        }, timeout=TIMEOUT), 'long-lived token exchange')
        token = long.get('access_token', '') or token
        expires = int(time.time()) + int(long.get('expires_in', 3600))
        profile = _json_response(requests.get(API + '/v1.0/me', params={
            'fields': 'id,username', 'access_token': token,
        }, timeout=TIMEOUT), 'profile lookup')
    except requests.RequestException as exc:
        raise ThreadsConnectionError('Could not reach Meta to complete authorization. Please try again.') from exc
    username = str(profile.get('username') or '')
    user_id = str(profile.get('id') or '')
    if not username or not user_id:
        raise ThreadsConnectionError('Could not verify the connected Threads account.')
    expected = os.getenv('THREADS_EXPECTED_USERNAME', '').lstrip('@').strip()
    if expected and not hmac.compare_digest(username.casefold(), expected.casefold()):
        raise ThreadsConnectionError(f'Connected account is not @{expected}. Please authorize the expected account.')
    conn.execute('''INSERT INTO threads_connection (id, user_id, username, access_token, obtained_at, expires_at, status)
       VALUES (1,?,?,?,?,?,'connected') ON CONFLICT(id) DO UPDATE SET
       user_id=excluded.user_id, username=excluded.username, access_token=excluded.access_token,
       obtained_at=excluded.obtained_at, expires_at=excluded.expires_at, status='connected' ''',
       (user_id, username, token, int(time.time()), expires))
    conn.commit()
    return username


def status(conn: sqlite3.Connection) -> dict | None:
    ensure_tables(conn)
    row = conn.execute('SELECT username, user_id, expires_at, status FROM threads_connection WHERE id=1').fetchone()
    if row is None:
        return None
    return {'username': row[0], 'user_id': row[1], 'expires_at': row[2], 'status': row[3]}


def disconnect(conn: sqlite3.Connection) -> None:
    ensure_tables(conn)
    # Keep the row as an explicit disabled marker: legacy Railway token must not reactivate scans.
    conn.execute('''INSERT INTO threads_connection (id,status) VALUES (1,'disconnected')
        ON CONFLICT(id) DO UPDATE SET access_token='', username='', user_id='', status='disconnected', expires_at=0''')
    conn.commit()


def token_for_scan(conn: sqlite3.Connection) -> tuple[str, str]:
    """Persisted OAuth credential is preferred; fall back to the legacy env token if unconfigured."""
    ensure_tables(conn)
    row = conn.execute('SELECT access_token, obtained_at, expires_at, status FROM threads_connection WHERE id=1').fetchone()
    if row:
        if row[3] != 'connected' or not row[0]:
            return '', 'Threads disconnected in dashboard.'
        if row[2] and int(row[2]) <= time.time() + 60:
            return '', 'Threads connection expired. Reconnect in the dashboard.'
        # Refresh long-lived tokens near expiry, but never log the token or provider response.
        # Meta only permits refresh after the token is at least 24 hours old.
        if row[2] and int(row[2]) - time.time() < 7 * 86400 and time.time() - int(row[1]) >= 86400:
            try:
                refresh = _json_response(requests.get(API + '/refresh_access_token', params={
                    'grant_type': 'th_refresh_token', 'access_token': row[0],
                }, timeout=TIMEOUT), 'token refresh')
                if refresh.get('access_token'):
                    fresh_token = refresh['access_token']
                    now = int(time.time())
                    conn.execute('UPDATE threads_connection SET access_token=?,obtained_at=?,expires_at=? WHERE id=1',
                                 (fresh_token, now, now + int(refresh.get('expires_in', 3600))))
                    conn.commit()
                    return fresh_token, 'Threads authorized through dashboard; token refreshed.'
            except (requests.RequestException, ThreadsConnectionError, ValueError):
                pass  # Continue with unexpired token; the next scan can retry.
        return row[0], 'Threads authorized through dashboard.'
    token = os.getenv('THREADS_ACCESS_TOKEN', '')
    return token, ('Threads using existing Railway token.' if token else 'Threads not connected.')


# Meta sends lifecycle webhooks as signed_request form POSTs to the two public
# HTTPS callbacks configured in Access the Threads API -> Settings.
def verify_meta_signed_request(signed_request: str) -> str:
    """Verify an authentic Meta callback and return its app-scoped user ID."""
    app_secret = os.getenv('THREADS_APP_SECRET', '').strip()
    if not app_secret or not isinstance(signed_request, str) or len(signed_request) > 10000:
        raise ThreadsConnectionError('Invalid Meta callback configuration or payload.')
    parts = signed_request.split('.')
    if len(parts) != 2 or not all(parts):
        raise ThreadsConnectionError('Malformed signed request.')
    sig_part, payload_part = parts
    try:
        signature = base64.urlsafe_b64decode(sig_part + '=' * (-len(sig_part) % 4))
        payload_bytes = base64.urlsafe_b64decode(payload_part + '=' * (-len(payload_part) % 4))
        payload = json.loads(payload_bytes)
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        raise ThreadsConnectionError('Invalid signed request encoding.') from exc
    expected = hmac.new(app_secret.encode('utf-8'), payload_part.encode('ascii'), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        # Some Meta clients sign decoded payload bytes; fallback is not enabled:
        # accept only the documented encoded-payload signed_request format.
        raise ThreadsConnectionError('Invalid signed request signature.')
    if not isinstance(payload, dict) or payload.get('algorithm', '').upper() != 'HMAC-SHA256':
        raise ThreadsConnectionError('Unsupported signed request algorithm.')
    user_id = str(payload.get('user_id') or '').strip()
    issued_at = payload.get('issued_at')
    if not user_id or len(user_id) > 128:
        raise ThreadsConnectionError('Missing user identifier in signed request.')
    if issued_at is not None:
        try:
            age = int(time.time()) - int(issued_at)
        except (TypeError, ValueError) as exc:
            raise ThreadsConnectionError('Invalid request timestamp.') from exc
        if age < -300 or age > 900:
            raise ThreadsConnectionError('Expired request timestamp.')
    return user_id


def _matching_connection(conn: sqlite3.Connection, user_id: str) -> bool:
    ensure_tables(conn)
    row = conn.execute('SELECT user_id FROM threads_connection WHERE id=1').fetchone()
    return bool(row and row[0] and hmac.compare_digest(str(row[0]), str(user_id)))


def handle_meta_uninstall(conn: sqlite3.Connection, user_id: str) -> None:
    """Prevent future scanner access when the authorized user revokes Meta access."""
    if _matching_connection(conn, user_id):
        disconnect(conn)


def handle_meta_delete(conn: sqlite3.Connection, user_id: str, base_url: str) -> dict:
    """Immediately erase Threads OAuth data and raw Threads candidate leads.

    This application has a single connected Threads profile and no per-post
    owner IDs. When that profile requests deletion, erase *all* stored Threads
    social leads conservatively rather than claiming it can distinguish authors.
    Independent verified event listings are kept because they are editorial
    records, not copies of the authorized user's account profile.
    """
    ensure_tables(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS threads_deletion_requests (
      confirmation_code TEXT PRIMARY KEY, created_at INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'completed'
    )""")
    if _matching_connection(conn, user_id):
        conn.execute("DELETE FROM social_leads WHERE platform='Threads'")
        disconnect(conn)
    code = secrets.token_hex(16)
    conn.execute('INSERT INTO threads_deletion_requests (confirmation_code,created_at,status) VALUES (?,?,?)',
                 (code, int(time.time()), 'completed'))
    conn.commit()
    return {'url': base_url.rstrip('/') + '/oauth/threads/deletion-status/' + code,
            'confirmation_code': code}


def get_meta_deletion_status(conn: sqlite3.Connection, code: str) -> str | None:
    if not re.fullmatch(r'[a-f0-9]{32}', code):
        return None
    conn.execute("""CREATE TABLE IF NOT EXISTS threads_deletion_requests (
      confirmation_code TEXT PRIMARY KEY, created_at INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'completed'
    )""")
    row = conn.execute('SELECT status FROM threads_deletion_requests WHERE confirmation_code=?', (code,)).fetchone()
    return str(row[0]) if row else None
