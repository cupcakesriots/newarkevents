"""Optional, permissioned Meta discovery connectors. Output is UNVERIFIED leads, never publishable events.

Only official Meta API endpoints are called. Public Facebook Events search is not supported.
No unofficial scraping, cookies, browser automation, or private content access.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests

ROOT = Path(__file__).resolve().parent
TIMEOUT = (10, 25)
HINTS = re.compile(r'\b(event|events|rsvp|tickets?|register|festival|concert|showcase|workshop|meetup|networking|pop[ -]?up|opening|exhibition|screening|performance|celebration|market|happening|artwalk|open mic|live music|party|join us|join me|save the date|admission|community day|book launch|panel)\b', re.I)
NEWARK = re.compile(r'\bnewark\b|\bbrick ?city\b|#newarkevents|#newarknj|#newarkarts', re.I)
OTHER_NEWARK = re.compile(r'\bnewark\s*,?\s*(?:de|delaware|oh|ohio|ca|california)\b', re.I)


def ensure_leads(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS social_leads (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      platform TEXT NOT NULL,
      original_post_url TEXT NOT NULL UNIQUE,
      post_text TEXT NOT NULL DEFAULT '',
      event_url TEXT NOT NULL DEFAULT '',
      image_url TEXT NOT NULL DEFAULT '',
      posted_at TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'needs_details',
      event_key TEXT NOT NULL DEFAULT '',
      discovered_at TEXT NOT NULL
    )''')
    conn.commit()


def safe_url(value: str) -> str:
    value = (value or '').strip()
    p = urlparse(value)
    if p.scheme not in ('https', 'http') or not p.netloc:
        return ''
    # Preserve functional ticket/Facebook identifiers, drop only known tracking parameters.
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not (k.lower().startswith('utm_') or k.lower() in ('fbclid', 'igsh', 'gclid'))]
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/') or '/', '', urlencode(query), ''))


def platform_for_url(value: str) -> str:
    host = urlparse(value).netloc.lower().removeprefix('www.')
    if host in ('instagram.com', 'instagr.am'):
        return 'Instagram'
    if host in ('threads.net', 'threads.com'):
        return 'Threads'
    if host in ('facebook.com', 'fb.com', 'm.facebook.com'):
        return 'Facebook'
    return 'Community link'


def is_event_candidate(text: str) -> bool:
    """Conservative *lead* screening, not event verification."""
    return bool(NEWARK.search(text or '') and HINTS.search(text or '') and not OTHER_NEWARK.search(text or ''))


def add_lead(conn, platform: str, url: str, text: str = '', event_url: str = '', image_url: str = '', posted_at: str = '') -> bool:
    original = safe_url(url)
    if not original:
        return False
    result = conn.execute('''INSERT OR IGNORE INTO social_leads
      (platform, original_post_url, post_text, event_url, image_url, posted_at, discovered_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)''', (
        platform[:60], original, (text or '')[:3000], safe_url(event_url),
        safe_url(image_url), posted_at[:45], datetime.now(timezone.utc).isoformat()
    ))
    conn.commit()
    return bool(result.rowcount)


def graph_get(url: str, token: str, params: dict) -> dict:
    # Avoid putting the access token in query strings (logs/analytics).
    response = requests.get(url, params=params, headers={'Authorization': f'Bearer {token}'}, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if 'error' in payload:
        raise RuntimeError(str(payload['error'].get('message', 'Meta API error')))
    return payload


def collect_threads(conn, queries: list[str], token: str, limit: int = 25) -> int:
    added = 0
    for q in queries[:12]:
        data = graph_get('https://graph.threads.net/keyword_search', token, {
            'q': q, 'search_type': 'RECENT', 'fields': 'id,text,permalink,timestamp,media_url,thumbnail_url,link_attachment_url',
            'limit': min(limit, 50)
        })
        for item in data.get('data', []):
            caption = item.get('text') or ''
            if not is_event_candidate(caption):
                continue
            added += add_lead(conn, 'Threads', item.get('permalink', ''), caption,
                              item.get('link_attachment_url', ''), item.get('thumbnail_url') or item.get('media_url') or '',
                              item.get('timestamp') or '')
    return added


def collect_instagram(conn, hashtags: list[str], token: str, ig_user_id: str, api_version: str, limit: int = 25) -> int:
    added = 0
    base = f'https://graph.facebook.com/{api_version}'
    for hashtag in hashtags[:20]:  # conservatively within Meta's limited rolling hashtag window
        name = hashtag.lstrip('#').strip()
        match = graph_get(f'{base}/ig_hashtag_search', token, {'user_id': ig_user_id, 'q': name})
        for tag in match.get('data', [])[:1]:
            hid = tag.get('id')
            if not hid:
                continue
            res = graph_get(f'{base}/{hid}/recent_media', token, {
                'user_id': ig_user_id, 'fields': 'id,caption,permalink,media_url,thumbnail_url,media_type,timestamp',
                'limit': min(limit, 50),
            })
            for item in res.get('data', []):
                caption = item.get('caption') or ''
                # Instagram hashtag context supports Newark, but the caption must still appear event-related.
                if not HINTS.search(caption):
                    continue
                if not (NEWARK.search(caption) or name.lower() in ('newarkevents', 'newarknj', 'newarkarts')):
                    continue
                added += add_lead(conn, 'Instagram', item.get('permalink', ''), caption, '',
                                  item.get('thumbnail_url') or item.get('media_url') or '', item.get('timestamp') or '')
    return added


def collect_facebook_pages(conn, pages: list[dict], api_version: str, limit: int = 25) -> int:
    added = 0
    base = f'https://graph.facebook.com/{api_version}'
    for page in pages:
        pid = str(page.get('page_id') or '').strip()
        var = str(page.get('token_env') or '').strip()
        token = os.getenv(var, '') if var else ''
        if not pid or not token:
            print(f'[social] Skipping Facebook Page {page.get("name", "(unnamed)")}: Page ID / token not configured')
            continue
        feed = graph_get(f'{base}/{pid}/posts', token, {
            'fields': 'id,message,created_time,permalink_url,full_picture,link', 'limit': min(limit, 50)
        })
        for item in feed.get('data', []):
            caption = item.get('message') or ''
            if not is_event_candidate(caption):
                continue
            added += add_lead(conn, 'Facebook', item.get('permalink_url') or '', caption,
                              item.get('link') or '', item.get('full_picture') or '', item.get('created_time') or '')
    return added


def collect_social(conn: sqlite3.Connection, config_path: Path | None = None) -> tuple[int, list[str]]:
    """Skip missing credentials instead of implying public unrestricted search is available."""
    ensure_leads(conn)
    path = config_path or ROOT / 'social_sources.json'
    cfg = json.loads(path.read_text(encoding='utf-8'))
    version = cfg.get('meta_graph_version', 'v26.0')
    enabled = []
    added = 0
    if os.getenv('THREADS_ACCESS_TOKEN'):
        try:
            n = collect_threads(conn, cfg.get('threads_queries', []), os.environ['THREADS_ACCESS_TOKEN'])
            added += n; enabled.append(f'Threads: {n} new lead(s)')
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            enabled.append(f'Threads ERROR: {exc}')
    else:
        enabled.append('Threads skipped: THREADS_ACCESS_TOKEN missing or permission not approved')
    if os.getenv('INSTAGRAM_ACCESS_TOKEN') and os.getenv('INSTAGRAM_IG_USER_ID'):
        try:
            n = collect_instagram(conn, cfg.get('instagram_hashtags', []), os.environ['INSTAGRAM_ACCESS_TOKEN'],
                                  os.environ['INSTAGRAM_IG_USER_ID'], version)
            added += n; enabled.append(f'Instagram: {n} new lead(s)')
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            enabled.append(f'Instagram ERROR: {exc}')
    else:
        enabled.append('Instagram skipped: professional IG account ID / approved token missing')
    try:
        n = collect_facebook_pages(conn, cfg.get('facebook_pages', []), version)
        added += n; enabled.append(f'Facebook authorized Pages: {n} new lead(s)')
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        enabled.append(f'Facebook ERROR: {exc}')
    return added, enabled
