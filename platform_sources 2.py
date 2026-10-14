"""Opt-in Luma/Eventbrite discovery from published feeds and authorized APIs.

There is intentionally no undocumented global search or platform bypass.
All new events are pending review; unconfirmed details are unverified leads.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import requests

from social import add_lead, ensure_leads

SOURCE_TYPES = {
    'luma_ical': 'Luma calendar iCal feed',
    'luma_event': 'Luma event link',
    'eventbrite_event': 'Eventbrite event link',
    'eventbrite_org': 'Eventbrite organization ID (API)',
}


def ensure_watches(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS source_watches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        value TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        UNIQUE (kind, value)
    )''')
    conn.commit()


def valid_source(kind: str, value: str) -> str:
    if kind not in SOURCE_TYPES:
        raise ValueError('Choose a supported source type')
    value = (value or '').strip()
    if kind == 'eventbrite_org':
        if not re.fullmatch(r'[0-9]{1,25}', value):
            raise ValueError('Eventbrite organization ID must contain digits only (not an organizer URL)')
        return value
    if value.lower().startswith('webcal://'):
        value = 'https://' + value[len('webcal://'):]
    p = urlparse(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port:
        raise ValueError('Please paste a public HTTPS link from the selected platform')
    host = p.hostname.lower()
    if kind.startswith('luma'):
        if not (host in ('luma.com', 'lu.ma') or host.endswith('.luma.com') or host.endswith('.lu.ma')):
            raise ValueError('Luma sources must use luma.com or lu.ma domains')
    elif kind == 'eventbrite_event':
        # Covers regional Eventbrite domains while excluding suffix lookalikes.
        if not re.fullmatch(r'(?:[a-z0-9-]+\.)*eventbrite\.(?:com|co\.uk|ca|com\.au|ie|de|fr|es|it|nl)', host):
            raise ValueError('Eventbrite event link must use an Eventbrite domain')
    if kind == 'eventbrite_event' and not re.search(r'/(?:e|tickets)/', p.path):
        raise ValueError('Paste a specific Eventbrite event link (normally /e/...)')
    if kind == 'luma_event' and (p.path in ('', '/', '/discover', '/calendar')):
        raise ValueError('Paste an individual Luma event, not a calendar homepage')
    # Don't remove Luma tokens / functional query fields from a subscriber feed.
    return value


def _api_get(url: str, token: str, params: dict | None = None) -> dict:
    response = requests.get(url, params=params or {}, headers={'Authorization': f'Bearer {token}', 'Accept':'application/json'}, timeout=(10, 25))
    response.raise_for_status()
    return response.json()


def _eventbrite_event(data: dict):
    from aggregator import Event, clean, local_dt, canonical_url
    if data.get('status') in ('canceled', 'draft', 'completed', 'ended') or data.get('online_event') or data.get('hide_start_date'):
        return None
    title = data.get('name') or ''
    if isinstance(title, dict): title = title.get('text') or ''
    start = data.get('start') or {}
    when = start.get('local') or start.get('utc') if isinstance(start, dict) else start
    when = local_dt(when)
    venue = data.get('venue') or {}
    if not isinstance(venue, dict): venue = {}
    addr = venue.get('address') or {}
    if not isinstance(addr, dict): addr = {}
    region = (addr.get('region') or '').strip()
    city = (addr.get('city') or '').strip()
    address = ', '.join(filter(None, [addr.get('address_1'), f'{city}, {region}' if city and region else city, addr.get('postal_code')]))
    logo = data.get('logo') or {}
    original_logo = (logo.get('original') or {}) if isinstance(logo, dict) else {}
    img = (original_logo.get('url') or logo.get('url') or '') if isinstance(logo, dict) else ''
    desc = data.get('summary') or data.get('description') or ''
    if isinstance(desc, dict): desc = desc.get('text') or ''
    link = canonical_url(data.get('url') or '')
    if not title or not when or not link: return None
    return Event(clean(title), when.isoformat(), clean(venue.get('name')), clean(address), clean(desc)[:600], link, link, img, 'Eventbrite')


def _collect_eventbrite_org(identifier: str, token: str):
    from aggregator import Event
    events: list[Event] = []
    next_token = None
    for _ in range(3):  # bounded pagination
        params = {'expand': 'venue,logo'}
        if next_token: params['continuation'] = next_token
        payload = _api_get(f'https://www.eventbriteapi.com/v3/organizations/{identifier}/events/', token, params)
        for item in payload.get('events', [])[:100]:
            if not isinstance(item, dict): continue
            ev = _eventbrite_event(item)
            if ev: events.append(ev)
        page = payload.get('pagination') or {}
        next_token = page.get('continuation') if page.get('has_more_items') else None
        if not next_token: break
    return events


def _collect_individual(kind: str, link: str, token: str):
    """Use structured Event markup on public pages; optional official Eventbrite API."""
    from aggregator import parse_event_page, canonical_url
    if kind == 'eventbrite_event' and token:
        # Numeric Eventbrite IDs are normally at end of /e/name-tickets-123456789.
        match = re.search(r'(?:tickets-|/e/)([0-9]{6,20})(?:[/?]|$)', link)
        if match:
            try:
                data = _api_get(f'https://www.eventbriteapi.com/v3/events/{match.group(1)}/', token, {'expand':'venue,logo'})
                ev = _eventbrite_event(data)
                if ev: return [ev]
            except (requests.RequestException, ValueError):
                pass  # Public page remains a possible fallback.
    events = parse_event_page(link, {'name': 'Luma' if kind == 'luma_event' else 'Eventbrite'})
    # Never replace source attribution with ticket-sale page of another site.
    for ev in events:
        ev.source = 'Luma' if kind == 'luma_event' else 'Eventbrite'
        ev.original_post_url = canonical_url(link)
    return events





# Some Luma iCal entries put the actual event link in DESCRIPTION, while the
# generic iCal reader falls back to the subscription URL if URL is absent.
# A subscription URL must never be used as an individual event's detail link.
_LUMA_LINK = re.compile(r'https?://(?:www\.)?(?:luma\.com|lu\.ma)/[A-Za-z0-9][A-Za-z0-9_/-]{2,100}', re.I)


def resolve_luma_event_url(event) -> str:
    """Resolve a public Luma event URL, preferring the iCal URL over DESCRIPTION.

    DESCRIPTION is used only if it contains a genuine individual Luma event URL;
    never assume the calendar feed is itself an event or invent a page URL.
    """
    from aggregator import canonical_url
    for field in ('original_post_url', 'event_url'):
        value = str(getattr(event, field, '') or '')
        if _luma_event_page(value):
            return canonical_url(value)
    for field in ('description', 'original_post_url', 'event_url'):
        value = str(getattr(event, field, '') or '')
        for match in _LUMA_LINK.finditer(value):
            candidate = canonical_url(match.group(0).rstrip('/.,;:'))
            if _luma_event_page(candidate):
                return candidate
    return ''


def _is_luma_feed_link(value: str) -> bool:
    p = urlparse(str(value or ''))
    return p.scheme in ('https', 'http') and p.hostname in (
        'api.luma.com', 'api2.luma.com', 'api.lu.ma', 'api2.lu.ma'
    ) and p.path.startswith('/ics/')


def normalize_luma_events(events: list) -> int:
    """Repair source URLs on new event objects without changing their identity."""
    resolved = 0
    for event in events:
        url = resolve_luma_event_url(event)
        if not url:
            continue
        if not _luma_event_page(event.original_post_url):
            event.original_post_url = url
        if not _luma_event_page(event.event_url):
            event.event_url = url
        resolved += 1
    return resolved


def _luma_event_page(url: str) -> bool:
    """Only fetch a real Luma event page, not an arbitrary iCal-provided URL."""
    parsed = urlparse(url or '')
    return (parsed.scheme == 'https' and parsed.hostname in
            ('luma.com', 'www.luma.com', 'lu.ma', 'www.lu.ma')
            and parsed.path not in ('', '/', '/discover', '/calendar'))


def _trusted_luma_image(page_url: str, raw: str) -> str:
    """Accept real Luma CDN images, never arbitrary third-party image hosts."""
    raw = (raw or '').strip()
    if not raw or raw.lower().startswith(('data:', 'javascript:')):
        return ''
    url = urljoin(page_url, raw)
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and (host == 'lumacdn.com' or host.endswith('.lumacdn.com')
                 or host in ('luma.com', 'www.luma.com', 'lu.ma', 'www.lu.ma'))):
        return url
    return ''


@lru_cache(maxsize=512)
def luma_cover_image(event_url: str) -> str:
    """Find an event cover on the public Luma page (OG preview if necessary).

    iCal feeds list events but normally omit images. This fetches the event's
    own public page; it uses no private API, login or access-control bypass.
    """
    if not _luma_event_page(event_url):
        return ''
    from aggregator import soup_for, meta, image_field, extract_jsonld
    soup = soup_for(event_url)
    # Prefer the actual cover to organizer avatars or branded social previews.
    for img in soup.find_all('img'):
        alt = (img.get('alt') or '').casefold()
        if 'cover image' not in alt and 'event cover' not in alt:
            continue
        for attr in ('src', 'data-src', 'data-original'):
            url = _trusted_luma_image(event_url, img.get(attr) or '')
            if url:
                return url
        srcset = img.get('srcset') or ''
        if srcset:
            url = _trusted_luma_image(event_url, srcset.split(',')[0].strip().split(' ')[0])
            if url:
                return url
    for candidate in (meta(soup, 'og:image'), meta(soup, 'twitter:image')):
        url = _trusted_luma_image(event_url, candidate)
        if url:
            return url
    for node in extract_jsonld(soup):
        url = _trusted_luma_image(event_url, image_field(node.get('image')))
        if url:
            return url
    return ''


def add_luma_images(conn: sqlite3.Connection, events: list, diagnostics: dict | None = None) -> tuple[int, int]:
    """Fill missing photos AND repair broken feed URLs on queued Luma cards.

    Never change moderation status, photo permission, or other event sources.
    Failures are counted in scan diagnostics rather than hidden as "0 found".
    """
    from aggregator import stable_key
    diagnostics = diagnostics if diagnostics is not None else {}
    found = 0
    updated = 0
    # Each watched feed is capped to avoid unbounded remote page requests.
    for ev in events[:65]:
        page_url = resolve_luma_event_url(ev)
        if not page_url:
            diagnostics['missing_event_url'] = diagnostics.get('missing_event_url', 0) + 1
            continue
        # Repair objects before upsert so new records link to the real event page.
        ev.original_post_url = page_url if not _luma_event_page(ev.original_post_url) else ev.original_post_url
        ev.event_url = page_url if not _luma_event_page(ev.event_url) else ev.event_url
        key = stable_key(ev)
        existing = conn.execute('SELECT image_url,source,status,event_url,original_post_url FROM events WHERE key=?',
                                (key,)).fetchone()
        if existing and existing['source'] == 'Luma' and existing['status'] in ('pending', 'approved'):
            # Fix older listings pointing to api.luma.com/ics/get (not an event page).
            if _is_luma_feed_link(existing['event_url']) or not existing['event_url']:
                conn.execute('UPDATE events SET event_url=? WHERE key=?', (page_url, key))
                diagnostics['fixed_detail_links'] = diagnostics.get('fixed_detail_links', 0) + 1
            if _is_luma_feed_link(existing['original_post_url']) or not existing['original_post_url']:
                conn.execute('UPDATE events SET original_post_url=? WHERE key=?', (page_url, key))
        if ev.image_url or (existing and existing['image_url']):
            continue
        if existing and (existing['source'] != 'Luma' or existing['status'] not in ('pending', 'approved')):
            continue
        try:
            cover = luma_cover_image(page_url)
        except Exception as exc:
            diagnostics['fetch_errors'] = diagnostics.get('fetch_errors', 0) + 1
            if not diagnostics.get('first_error'):
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                diagnostics['first_error'] = f'{type(exc).__name__} HTTP {status}' if status else type(exc).__name__
            continue
        if not cover:
            diagnostics['no_cover'] = diagnostics.get('no_cover', 0) + 1
            continue
        ev.image_url = cover
        found += 1
        result = conn.execute("""UPDATE events SET image_url=?
            WHERE key=? AND source='Luma' AND status IN ('pending','approved')
            AND (image_url IS NULL OR trim(image_url)='')""", (cover, key))
        updated += result.rowcount
    return found, updated


def run_watches(conn: sqlite3.Connection, now, days: int) -> tuple[int, list[str]]:
    from aggregator import scan_ical, qualify, upsert, is_newark
    ensure_watches(conn)
    ensure_leads(conn)
    rows = conn.execute('SELECT kind,value FROM source_watches WHERE enabled=1 ORDER BY id LIMIT 100').fetchall()
    token = os.environ.get('EVENTBRITE_ACCESS_TOKEN', '')
    added = 0
    messages: list[str] = []
    for kind, identifier in rows:
        label = SOURCE_TYPES[kind]
        try:
            if kind == 'luma_ical':
                events = scan_ical({'name':'Luma', 'url': identifier}, now, days)
            elif kind == 'eventbrite_org':
                if not token:
                    messages.append(f'Eventbrite organization {identifier}: skipped (EVENTBRITE_ACCESS_TOKEN not set)')
                    continue
                events = _collect_eventbrite_org(identifier, token)
            else:
                try:
                    events = _collect_individual(kind, identifier, token)
                except (requests.RequestException, ValueError) as exc:
                    n = add_lead(conn, 'Luma' if kind == 'luma_event' else 'Eventbrite', identifier,
                                 f'Event link needs details: Could not fetch verified date and Newark address ({type(exc).__name__}).')
                    added += 0
                    messages.append(f'{label}: page unavailable; {n} unverified lead(s)')
                    continue
            # A page can load but expose no structured date/location metadata.
            # Keep its link as a manual lead instead of silently dropping it.
            if kind in ('luma_event', 'eventbrite_event') and not events:
                n = add_lead(conn, 'Luma' if kind == 'luma_event' else 'Eventbrite', identifier,
                             'Event link added. Date, time and Newark address need to be confirmed from the organizer.')
                messages.append(f'{label}: no machine-readable event details; {int(n)} unverified lead(s)')
                continue
            qualifying = [ev for ev in events if qualify(ev, now, days)]
            photo_note = ''
            if kind in ('luma_ical', 'luma_event'):
                diagnostics = {}
                resolved = normalize_luma_events(qualifying)
                photos_found, photos_backfilled = add_luma_images(conn, qualifying, diagnostics)
                photo_note = (f', {resolved} real event link(s), {photos_found} cover image(s) found, '
                              f'{photos_backfilled} existing card(s) updated, '
                              f'{diagnostics.get("fixed_detail_links", 0)} old detail link(s) repaired')
                if diagnostics.get('missing_event_url'):
                    photo_note += f', {diagnostics["missing_event_url"]} without event page URL'
                if diagnostics.get('fetch_errors'):
                    photo_note += f', {diagnostics["fetch_errors"]} image page request error(s) ({diagnostics["first_error"]})'
                if diagnostics.get('no_cover'):
                    photo_note += f', {diagnostics["no_cover"]} page(s) without usable cover metadata'
            count = sum(upsert(conn, ev) for ev in qualifying)
            added += count
            # Avoid assuming all events from a Luma calendar or Eventbrite org are physically in Newark.
            needs_details = 0
            for ev in events:
                if qualify(ev, now, days) or is_newark(ev): continue
                # Leads only where Newark explicitly appears, or a specific event URL is watched.
                if kind in ('luma_event','eventbrite_event') or 'newark' in (ev.address+' '+ev.venue+' '+ev.title+' '+ev.description).lower():
                    needs_details += int(add_lead(conn, ev.source, ev.original_post_url or ev.event_url,
                        f'{ev.title}\nStarts: {ev.start}\nLocation not verified as Newark, NJ: {ev.address or ev.venue}\n{ev.description}', ev.event_url, ev.image_url))
            messages.append(f'{label}: {len(qualifying)} Newark events, {count} new pending, {needs_details} unverified lead(s){photo_note}')
        except Exception as exc:
            messages.append(f'{label}: ERROR {type(exc).__name__} ({str(exc)[:170]})')
    conn.commit()
    if not rows:
        messages.append('Luma/Eventbrite: no watched sources configured; add them under Sources in the dashboard')
    return added, messages
