"""Optional Newark-area Eventbrite *link discovery* using a licensed web search API.

Does NOT access Eventbrite pages or an undocumented Eventbrite API endpoint.
Does NOT verify event dates/locations or obtain/reuse event cover images.
Every result goes to Social Leads for human checking, never to the approval queue.

To persist search-result links, confirm your Brave plan grants storage rights.
Set BRAVE_SEARCH_API_KEY and BRAVE_STORAGE_RIGHTS_CONFIRMED=1.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from social import add_lead, safe_url, ensure_leads

SEARCH_ENDPOINT = 'https://api.search.brave.com/res/v1/web/search'
EVENTBRITE_HOST = re.compile(r'^(?:[a-z0-9-]+\.)*eventbrite\.com$', re.I)
EVENT_PATH = re.compile(r'^/e/[^/]+', re.I)
NEWARK_HINT = re.compile(r'\bnewark\b|\b071(?:0\d|1[0-4])\b', re.I)
OTHER_NEWARK = re.compile(r'\bnewark\s*,?\s*(?:de|delaware|oh|ohio|ca|california)\b', re.I)


def is_eventbrite_event_url(url: str) -> bool:
    p = urlparse(safe_url(url))
    return (p.scheme == 'https' and bool(EVENTBRITE_HOST.fullmatch(p.hostname or ''))
            and bool(EVENT_PATH.match(p.path)))


def parse_candidates(payload: dict):
    """Read search-index hits only; leave unverifiable findings as leads."""
    for hit in ((payload.get('web') or {}).get('results') or []):
        if not isinstance(hit, dict):
            continue
        url = safe_url(hit.get('url') or '')
        if not is_eventbrite_event_url(url):
            continue
        title = str(hit.get('title') or '')
        snippet = str(hit.get('description') or '')
        evidence = title + ' ' + snippet
        if OTHER_NEWARK.search(evidence) or not NEWARK_HINT.search(evidence):
            continue
        yield url, title, snippet


def run_eventbrite_newark_discovery(conn, session=None) -> list[str]:
    """Return scan messages; never raise secrets or write unlicensed search results."""
    ensure_leads(conn)
    key = os.getenv('BRAVE_SEARCH_API_KEY', '').strip()
    if not key:
        return ['Eventbrite Newark search: skipped (BRAVE_SEARCH_API_KEY not set; organizer login is not needed)']
    if os.getenv('BRAVE_STORAGE_RIGHTS_CONFIRMED', '').strip() != '1':
        return ['Eventbrite Newark search: skipped (confirm a Brave Search API plan allowing persistent storage of search results; set BRAVE_STORAGE_RIGHTS_CONFIRMED=1 only after confirming rights)']
    s = session or requests
    year = datetime.now(ZoneInfo('America/New_York')).year
    queries = [
        f'site:eventbrite.com/e/ "Newark" "NJ" "{year}"',
        f'site:eventbrite.com/e/ "Newark" "07102" "{year}"',
        f'site:eventbrite.com/e/ "Newark" "07105" "{year}"',
    ]
    added = 0
    seen = set()
    errors = 0
    for query in queries:
        try:
            response = s.get(SEARCH_ENDPOINT,
                headers={'X-Subscription-Token': key, 'Accept': 'application/json'},
                params={'q':query, 'country':'US', 'search_lang':'en', 'count':20},
                timeout=(10, 20))
            response.raise_for_status()
            data = response.json()
            for url, title, snippet in parse_candidates(data):
                if url in seen:
                    continue
                seen.add(url)
                # Search result text is an unverified search excerpt, NOT organizer-supplied event metadata.
                note = (f'Candidate Eventbrite listing: {title[:180]}\n'
                        f'Indexed excerpt (unverified): {snippet[:500]}\n'
                        'Verify future event date, Newark NJ street address, and image reuse permission from the organizer before approval.')
                added += int(add_lead(conn, 'Eventbrite', url, note, event_url=url))
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors += 1
            print(f'[eventbrite] Search provider request failed: {type(exc).__name__} (no secret printed)')
    return [f'Eventbrite Newark web discovery: {added} new unverified lead(s), {len(seen)} distinct indexed links checked, {errors} search request error(s). No Eventbrite pages scraped; no events approved.']
