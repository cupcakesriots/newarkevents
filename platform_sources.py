"""Opt-in Luma/Eventbrite discovery from published feeds and authorized APIs.

There is intentionally no undocumented global search or platform bypass.
All new events are pending review; unconfirmed details are unverified leads.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

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
            messages.append(f'{label}: {len(qualifying)} Newark events, {count} new pending, {needs_details} unverified lead(s)')
        except Exception as exc:
            messages.append(f'{label}: ERROR {type(exc).__name__} ({str(exc)[:170]})')
    conn.commit()
    if not rows:
        messages.append('Luma/Eventbrite: no watched sources configured; add them under Sources in the dashboard')
    return added, messages
