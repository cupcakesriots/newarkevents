"""Transient Eventbrite link previews using Brave's authorized Web Search API.

DOES NOT persist, cache, export or index search results. Only a single
requested lookup is kept in memory for the immediate HTTP response.
Users must independently verify each event on its source website before
manually entering it in the editorial event review queue.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

SEARCH_ENDPOINT = 'https://api.search.brave.com/res/v1/web/search'
NEWARK_HINT = re.compile(r'\bnewark\b|\b071(?:0\d|1[0-4])\b', re.I)
OTHER_NEWARK = re.compile(r'\bnewark\s*,?\s*(?:de|delaware|oh|ohio|ca|california)\b', re.I)
SEARCH_MODES = {
    'general': 'Events around Newark NJ',
    'business': 'Networking, tech and entrepreneurship',
    'arts': 'Arts, culture and community',
    'food': 'Food, markets and pop-ups',
}


def eventbrite_link(raw):
    """Only allow HTTPS links to actual Eventbrite event pages."""
    if not isinstance(raw, str): return ''
    try:
        p = urlparse(raw)
        host = (p.hostname or '').lower()
        if p.scheme != 'https' or not (host == 'eventbrite.com' or host.endswith('.eventbrite.com')):
            return ''
        if p.username or p.password or p.port not in (None,443):
            return ''
        if not re.match(r'^/e/[^/]+', p.path, re.I):
            return ''
        return raw[:1600]
    except ValueError:
        return ''


def make_query(mode):
    if mode not in SEARCH_MODES:
        raise ValueError('Unknown search category')
    now = datetime.now(ZoneInfo('America/New_York'))
    terms = {
        'general': 'events',
        'business': 'networking tech entrepreneur',
        'arts': 'art culture community',
        'food': 'food market pop-up',
    }
    # Search engines can return past events even with a date term. Do not claim future verification.
    return f'site:eventbrite.com/e/ "Newark" "NJ" {terms[mode]} {now.year}'


def preview_search(mode, session=None):
    """Fetch ONE request, return unverified links in memory only; never persist.

    The caller must not log payload, store it, or create leads from its contents.
    """
    key = os.environ.get('BRAVE_SEARCH_API_KEY','').strip()
    if not key:
        return [], 'BRAVE_SEARCH_API_KEY is not set in Railway. Add it under Service → Variables.'
    try:
        response = (session or requests).get(
            SEARCH_ENDPOINT,
            headers={'Accept':'application/json','X-Subscription-Token':key, 'Cache-Control':'no-cache'},
            params={'q': make_query(mode),'count':20,'country':'US','search_lang':'en','result_filter':'web'},
            timeout=(5,18))
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 'unknown'
        return [], f'Brave search returned HTTP {status}. Check your Search API key and plan.'
    except (requests.RequestException, ValueError, TypeError):
        return [], 'Brave search could not be completed. Please retry later.'
    results = []
    seen = set()
    for hit in ((payload.get('web') or {}).get('results') or []):
        if not isinstance(hit, dict): continue
        url = eventbrite_link(hit.get('url'))
        if not url or url in seen: continue
        title = str(hit.get('title') or '')
        excerpt = str(hit.get('description') or '')
        evidence = f'{title} {excerpt}'
        if not NEWARK_HINT.search(evidence) or OTHER_NEWARK.search(evidence):
            continue
        seen.add(url)
        results.append({'url':url,'title':title[:180] or 'Eventbrite event link','snippet':excerpt[:340]})
    # No global, database, session, file, or job-store writes.
    return results, ''
