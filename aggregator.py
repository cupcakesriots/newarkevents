"""Newark event collector and Reddit publishing queue. Only `publish --live` writes to Reddit."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from dotenv import load_dotenv
from icalendar import Calendar
import recurring_ical_events

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('America/New_York')
USER_AGENT = 'NewarkEventsDiscovery/0.1 (community events bot; contact subreddit moderators)'
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': USER_AGENT, 'Accept': 'text/html, application/xml, text/calendar, */*'})
HTML_CACHE: dict[str, str] = {}

@dataclass
class Event:
    title: str
    start: str
    venue: str
    address: str
    description: str
    event_url: str
    original_post_url: str
    image_url: str
    source: str
    image_approved: int = 0


def clean(s: object) -> str:
    return re.sub(r'\s+', ' ', html.unescape(str(s or ''))).strip()


def canonical_url(url: str) -> str:
    url = clean(url)
    if not url or not urlparse(url).scheme.startswith('http'):
        return ''
    p = urlparse(url)
    query = parse_qs(p.query)
    # Preserve museum date query; it identifies different event occurrences.
    keep = {k: v for k, v in query.items() if k.lower() in ('date', 'eventid', 'event_id', 'event', 'eid', 'session', 'occurrence', 'id')}
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/') or '/', '', urlencode(keep, doseq=True), ''))


def local_dt(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        d = value
    elif isinstance(value, date):
        d = datetime.combine(value, dtime(0))
    else:
        try:
            d = dateparser.parse(str(value).replace('@', ' '))
        except (TypeError, ValueError, OverflowError):
            return None
    if d is None:
        return None
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d.astimezone(TZ)


def stable_key(ev: Event) -> str:
    # Cross-source duplicate detection: normalized title + LOCAL event date/time.
    normalized = re.sub(r'[^a-z0-9]+', '', ev.title.casefold())
    d = local_dt(ev.start)
    return hashlib.sha256(f'{normalized}|{d.strftime("%Y-%m-%dT%H:%M") if d else ""}'.encode()).hexdigest()[:20]


def fetch(url: str, *, binary: bool = False):
    r = SESSION.get(url, timeout=(10, 25), allow_redirects=True)
    r.raise_for_status()
    if len(r.content) > 8_000_000:
        raise ValueError('Source too large')
    return r.content if binary else r.text


def soup_for(url: str) -> BeautifulSoup:
    if url not in HTML_CACHE:
        HTML_CACHE[url] = fetch(url)
    return BeautifulSoup(HTML_CACHE[url], 'html.parser')


def meta(soup: BeautifulSoup, key: str) -> str:
    tag = soup.find('meta', attrs={'property': key}) or soup.find('meta', attrs={'name': key})
    return clean(tag.get('content')) if tag else ''


def extract_jsonld(soup: BeautifulSoup) -> list[dict]:
    def walk(node):
        if isinstance(node, list):
            for item in node:
                yield from walk(item)
        elif isinstance(node, dict):
            typ = node.get('@type', [])
            types = [typ] if isinstance(typ, str) else typ
            if any(str(t).lower().endswith('event') for t in types):
                yield node
            if '@graph' in node:
                yield from walk(node['@graph'])
    found = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            found.extend(walk(json.loads(tag.string or tag.get_text())))
        except (ValueError, TypeError):
            continue
    return found


def location_fields(location) -> tuple[str, str]:
    if isinstance(location, list):
        location = location[0] if location else {}
    if isinstance(location, str):
        return clean(location), clean(location)
    if not isinstance(location, dict):
        return '', ''
    a = location.get('address') or {}
    addr = ', '.join(clean(a.get(k)) for k in ('streetAddress', 'addressLocality', 'addressRegion', 'postalCode') if a.get(k)) if isinstance(a, dict) else clean(a)
    return clean(location.get('name')), addr


def image_field(val) -> str:
    if isinstance(val, list):
        val = val[0] if val else ''
    if isinstance(val, dict):
        val = val.get('url') or val.get('contentUrl') or ''
    return clean(val)


def first_external_link(soup: BeautifulSoup, page: str) -> str:
    # Ticket sources are curated conservatively rather than selecting random links.
    for a in soup.find_all('a', href=True):
        text = clean(a.get_text(' ', strip=True)).lower()
        if re.search(r'get tickets|buy tickets|register|rsvp', text):
            href = urljoin(page, a['href'])
            if href.startswith('https://'):
                return href
    return ''


def parse_event_page(url: str, source: dict) -> list[Event]:
    soup = soup_for(url)
    og_image = urljoin(url, meta(soup, 'og:image')) if meta(soup, 'og:image') else ''
    og_desc = meta(soup, 'og:description') or meta(soup, 'description')
    name = source['name']
    events = []
    for node in extract_jsonld(soup):
        title = clean(node.get('name'))
        start = local_dt(node.get('startDate'))
        if name == 'Newark Museum of Art':
            occurrence = (parse_qs(urlparse(url).query).get('date') or [''])[0]
            if re.fullmatch(r'\d{12}', occurrence):
                start = datetime.strptime(occurrence, '%Y%m%d%H%M').replace(tzinfo=TZ)
        if not title or not start:
            continue
        venue, address = location_fields(node.get('location'))
        desc = BeautifulSoup(str(node.get('description') or ''), 'html.parser').get_text(' ', strip=True)
        offers = node.get('offers') or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        ticket_url = (offers.get('url') or '') if isinstance(offers, dict) else ''
        ev = Event(title, start.isoformat(), venue or source.get('default_venue', ''), address or source.get('default_address', ''), clean(desc or og_desc)[:600], canonical_url(url), canonical_url(url), image_field(node.get('image')) or og_image, name)
        if ticket_url and urlparse(ticket_url).scheme == 'https':
            ev.event_url = ticket_url
        events.append(ev)
    if events:
        return events

    # Fallback for sites without Event JSON-LD. Do not invent times.
    page_title = clean((soup.find('h1') or soup.find('title')).get_text(' ', strip=True)) if (soup.find('h1') or soup.find('title')) else ''
    dates = []
    if name == 'Newark Museum of Art':
        # Museum event URLs include an occurrence identifier: ?date=202610151700.
        value = (parse_qs(urlparse(url).query).get('date') or [''])[0]
        if re.fullmatch(r'\d{12}', value):
            dates = [datetime.strptime(value, '%Y%m%d%H%M').replace(tzinfo=TZ)]
    elif name == 'NJPAC':
        # NJPAC individual event pages show e.g. 'Fri, 10/16/26 @ 8:00PM'.
        body = soup.get_text(' ', strip=True)
        seen = set()
        for m in re.finditer(r'\b\d{1,2}/\d{1,2}/\d{2,4}\s*@\s*\d{1,2}:\d{2}\s*[AP]M\b', body, re.I):
            when = local_dt(m.group())
            if when and when.isoformat() not in seen:
                dates.append(when)
                seen.add(when.isoformat())
    description = og_desc or ''
    if not description:
        main = soup.select_one('main') or soup
        description = clean(main.get_text(' ', strip=True))[:600]
    for d in dates[:6]:
        events.append(Event(page_title, d.isoformat(), source.get('default_venue', ''), source.get('default_address', ''), description[:600], first_external_link(soup, url) or canonical_url(url), canonical_url(url), og_image, name))
    return events


def scan_websites(source: dict) -> list[Event]:
    url = source['url']
    listing = soup_for(url)
    prefix = source['event_path_prefix']
    links = []
    seen = set()
    for a in listing.find_all('a', href=True):
        href = urljoin(url, a.get('href'))
        p = urlparse(href)
        if p.netloc.lower().removeprefix('www.') != urlparse(url).netloc.lower().removeprefix('www.'):
            continue
        if not p.path.startswith(prefix) or href in seen:
            continue
        seen.add(href)
        links.append(href)
        if len(links) >= int(source.get('max_links', 35)):
            break
    results = []
    for i, link in enumerate(links):
        try:
            results.extend(parse_event_page(link, source))
        except Exception as exc:
            print(f'[skip] {source["name"]} {link}: {exc}', file=sys.stderr)
        if i != len(links) - 1:
            time.sleep(.15)
    return results


def scan_ical(source: dict, now: datetime, days: int) -> list[Event]:
    cal = Calendar.from_ical(fetch(source['url'], binary=True))
    occurrences = recurring_ical_events.of(cal).between(now, now + timedelta(days=days))
    events = []
    for component in occurrences:
        title = clean(component.get('summary'))
        dtstart = component.decoded('dtstart', None)
        d = local_dt(dtstart)
        if not d or not title:
            continue
        desc = clean(component.get('description'))
        loc = clean(component.get('location'))
        original = clean(component.get('url'))
        if not original:
            original = source['url']
        events.append(Event(title, d.isoformat(), loc, loc, desc[:600], canonical_url(original), canonical_url(original), '', source['name']))
    return events


def scan_rss(source: dict) -> list[Event]:
    feed = feedparser.parse(fetch(source['url'], binary=True))
    events = []
    for entry in feed.entries:
        link = clean(entry.get('link'))
        if not link:
            continue
        # RSS articles are NOT assumed to have valid event dates; only structured event pages are queued.
        try:
            events.extend(parse_event_page(link, source))
        except Exception as exc:
            print(f'[skip] RSS {link}: {exc}', file=sys.stderr)
    return events


def scan_manual(path: Path) -> list[Event]:
    if not path.exists():
        return []
    result = []
    with path.open(newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            d = local_dt(row.get('start'))
            if not row.get('title') or not d or not row.get('event_url'):
                continue
            result.append(Event(clean(row['title']), d.isoformat(), clean(row.get('venue')), clean(row.get('address')), clean(row.get('description'))[:600], canonical_url(row['event_url']), canonical_url(row.get('original_post_url')) or canonical_url(row['event_url']), clean(row.get('image_url')), 'Community / manual', int(str(row.get('image_approved', '')).lower() in ('1','yes','true'))))
    return result


def is_newark(ev: Event, assume_newark=False) -> bool:
    location = (ev.address + ' ' + ev.venue).casefold()
    # Prevent Newark, DE or Newark, OH from slipping in.
    if re.search(r'newark\s*,?\s*(de|delaware|oh|ohio|ca|california)\b', location):
        return False
    return assume_newark or bool(re.search(r'newark\s*,?\s*(nj|new jersey|071\d{2})\b', location))


def qualify(ev: Event, now: datetime, days: int, assume_newark=False) -> bool:
    d = local_dt(ev.start)
    return bool(ev.title and ev.event_url and d and now <= d <= now + timedelta(days=days) and is_newark(ev, assume_newark))


def open_db(path: Path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS events (
        key TEXT PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL, venue TEXT,
        address TEXT, description TEXT, event_url TEXT, original_post_url TEXT,
        image_url TEXT, source TEXT, status TEXT NOT NULL DEFAULT 'pending',
        image_approved INTEGER NOT NULL DEFAULT 0, reddit_url TEXT, created_at TEXT NOT NULL
    )''')
    return conn


def upsert(conn, ev: Event) -> bool:
    k = stable_key(ev)
    if conn.execute('SELECT 1 FROM events WHERE key=?', (k,)).fetchone():
        return False  # Keep moderation decisions and original attribution intact.
    conn.execute('''INSERT INTO events (key,title,start,venue,address,description,event_url,original_post_url,image_url,source,image_approved,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (k, ev.title, ev.start, ev.venue, ev.address, ev.description, ev.event_url, ev.original_post_url, ev.image_url, ev.source, ev.image_approved, datetime.now(timezone.utc).isoformat()))
    return True


def discover(args):
    now = datetime.now(TZ)
    config = json.loads((ROOT / 'sources.json').read_text())
    conn = open_db(Path(args.db))
    load_dotenv(ROOT / '.env')
    sources = []
    for typ in ('ical', 'rss', 'websites'):
        sources.extend((typ, s) for s in config.get(typ, []))
    count, failures = 0, 0
    for typ, source in sources:
        try:
            if typ == 'ical':
                evs = scan_ical(source, now, args.days)
            elif typ == 'rss':
                evs = scan_rss(source)
            else:
                evs = scan_websites(source)
            selected = [e for e in evs if qualify(e, now, args.days, source.get('assume_newark', False))]
            for ev in selected:
                is_new = upsert(conn, ev)
                count += int(is_new)
            print(f'{source["name"]}: {len(selected)} upcoming qualifying events, {count} new total')
        except Exception as exc:
            failures += 1
            print(f'[ERROR] {source["name"]}: {exc}', file=sys.stderr)
    from platform_sources import run_watches
    watched_added, watched_messages = run_watches(conn, now, args.days)
    count += watched_added
    for message in watched_messages:
        print('[platform] ' + message)
    for ev in scan_manual(ROOT / 'manual_events.csv'):
        if qualify(ev, now, args.days):
            count += upsert(conn, ev)
    conn.commit()
    from social import collect_social
    social_added, social_messages = collect_social(conn)
    for message in social_messages:
        print('[social] ' + message)
    export_review(conn, Path(args.review))
    print(f'Added {count} verified event(s), {social_added} social lead(s), {failures} calendar source error(s). Open the dashboard to review. No Reddit posts made.')


REVIEW_FIELDS = ['key','decision','image_approved','title','start','venue','address','description','event_url','original_post_url','image_url','source']


def export_review(conn, path: Path):
    rows = conn.execute("SELECT * FROM events WHERE status IN ('pending','approved') ORDER BY start LIMIT 300").fetchall()
    existing = {}
    if path.exists():
        with path.open(newline='', encoding='utf-8-sig') as f:
            existing = {r['key']: r for r in csv.DictReader(f)}
    with path.open('w', newline='', encoding='utf-8') as f:
        out = csv.DictWriter(f, fieldnames=REVIEW_FIELDS)
        out.writeheader()
        for r in rows:
            base = {k: r[k] if k in r.keys() else '' for k in REVIEW_FIELDS}
            saved = existing.get(r['key'], {})
            base['decision'] = r['status']
            base['image_approved'] = str(r['image_approved'])
            out.writerow(base)
    # Visual preview for organizers, while CSV remains the editable moderation queue.
    cards = []
    for r in rows:
        img = f'<img src=\"{html.escape(r["image_url"], quote=True)}\" alt=\"Event flyer\" loading=\"lazy\">' if r['image_url'] and urlparse(r['image_url']).scheme in ('https', 'http') else '<div class=\"noimg\">No flyer available</div>'
        cards.append(f'''<article>{img}<section><small>{html.escape(r['start'][:16])} • {html.escape(r['source'])}</small><h2>{html.escape(r['title'])}</h2><p>{html.escape(r['venue'] or r['address'] or 'Venue to confirm')}</p><p>{html.escape(r['description'][:270])}</p><p><a href=\"{html.escape(r['event_url'],quote=True)}\" target=\"_blank\" rel=\"noopener\">Event details / tickets ↗</a> · <a href=\"{html.escape(r['original_post_url'],quote=True)}\" target=\"_blank\" rel=\"noopener\">Original post ↗</a></p><small>Review ID: {r['key']}</small></section></article>''')
    preview = '''<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Newark Events Review</title><style>body{font:16px system-ui,sans-serif;background:#f4f4f6;color:#20232a;max-width:1100px;margin:36px auto;padding:0 18px}h1{font-size:2em}header p{max-width:800px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}article{border:1px solid #ddd;border-radius:15px;background:white;overflow:hidden}article img,.noimg{height:180px;width:100%;object-fit:cover;background:#e5e7eb;display:grid;place-items:center;color:#5d6470}section{padding:17px}h2{font-size:1.18em;margin:8px 0}p{line-height:1.4}small{color:#666}a{color:#195bb0}</style><header><h1>Newark Events Review Queue</h1><p>Visual export only. Use the interactive dashboard (python dashboard.py) to approve events and edit details.</p></header><main>''' + '\n'.join(cards) + '</main></html>'
    path.with_suffix('.html').write_text(preview, encoding='utf-8')
    print(f'Review queue exported: {path} and {path.with_suffix(".html")} ({len(rows)} event(s))')


def import_review(conn, path: Path):
    if not path.exists():
        raise FileNotFoundError(f'Review file missing: {path}; run discover first')
    changed = 0
    with path.open(newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            decision = clean(row.get('decision')).lower()
            if decision not in ('pending', 'approved', 'rejected', 'approve', 'reject'):
                continue
            decision = {'approve':'approved','reject':'rejected'}.get(decision, decision)
            image_approved = int(clean(row.get('image_approved')).lower() in ('1', 'true', 'yes'))
            cur = conn.execute("UPDATE events SET status=?, image_approved=? WHERE key=? AND status != 'posted'", (decision, image_approved, row['key']))
            changed += cur.rowcount
    conn.commit()
    print(f'Imported decisions for {changed} queue item(s).')


def make_title(row) -> str:
    start = local_dt(row['start'])
    prefix = start.strftime('%a %b ') + str(start.day) if start else 'Upcoming'
    return f'[{prefix}] {row["title"]} | Newark, NJ'[:298]


def make_body(row) -> str:
    d = local_dt(row['start'])
    time_value = d.strftime('%A, %B ') + str(d.day) + d.strftime(', %Y at %I:%M %p %Z') if d else 'See organizer'
    parts = [f'**When:** {time_value}', f'**Where:** {row["venue"] or "Venue TBA"}' + (f' | {row["address"]}' if row['address'] and row['address'] != row['venue'] else '')]
    if row['description']:
        parts.append(f'**About:** {clean(row["description"])[:450]}')
    parts.append(f'**Event details / tickets:** {row["event_url"]}')
    if row['original_post_url'] and row['original_post_url'] != row['event_url']:
        parts.append(f'**Original announcement:** {row["original_post_url"]}')
    if row['image_url']:
        parts.append(f'**Event image / flyer:** {row["image_url"]}')
    parts.append(f'**Source:** {row["source"]}')
    parts.append('*Automatically collected, reviewed before posting. Confirm details with the organizer before attending.*')
    return '\n\n'.join(parts)


def approved_image_file(url: str) -> Path:
    p = urlparse(url)
    if p.scheme != 'https':
        raise ValueError('Image URL must use HTTPS')
    r = SESSION.get(url, stream=True, timeout=(10, 20))
    r.raise_for_status()
    ctype = r.headers.get('Content-Type','').lower().split(';')[0]
    ext = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}.get(ctype)
    if not ext:
        r.close()
        raise ValueError(f'Unsupported image format: {ctype}')
    with tempfile.NamedTemporaryFile(prefix='newark_event_', suffix=ext, delete=False) as f:
        size = 0
        for chunk in r.iter_content(32768):
            size += len(chunk)
            if size > 10_000_000:
                f.close()
                Path(f.name).unlink(missing_ok=True)
                raise ValueError('Image larger than 10MB')
            f.write(chunk)
        return Path(f.name)


def publish(args):
    conn = open_db(Path(args.db))
    if args.from_csv:
        import_review(conn, Path(args.review))
    rows = conn.execute("SELECT * FROM events WHERE status='approved' ORDER BY start LIMIT ?", (args.limit,)).fetchall()
    if not rows:
        print('No approved, unpublished events.')
        return
    if not args.live:
        for r in rows:
            print('\n' + '=' * 60)
            print('DRY RUN: ' + make_title(r))
            print(make_body(r))
            print(f'Image upload approved: {bool(r["image_approved"])}')
        print('\nNo Reddit posts made. Re-run with --live after reviewing.')
        return
    load_dotenv(ROOT / '.env')
    import praw
    from praw.models import InlineImage, PostMedia
    required = ['REDDIT_CLIENT_ID','REDDIT_CLIENT_SECRET','REDDIT_USERNAME','REDDIT_PASSWORD']
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError('Missing Reddit credentials: ' + ', '.join(missing))
    reddit = praw.Reddit(client_id=os.environ['REDDIT_CLIENT_ID'], client_secret=os.environ['REDDIT_CLIENT_SECRET'], username=os.environ['REDDIT_USERNAME'], password=os.environ['REDDIT_PASSWORD'], user_agent=os.getenv('REDDIT_USER_AGENT', 'newark-events-aggregator/0.1'))
    sub = reddit.subreddit(os.getenv('REDDIT_SUBREDDIT', 'newarkevents'))
    for r in rows:
        body = make_body(r)
        imagepath = None
        try:
            if os.getenv('ENABLE_NATIVE_IMAGES', 'true').lower() == 'true' and r['image_approved'] and r['image_url']:
                try:
                    imagepath = approved_image_file(r['image_url'])
                    body += '\n\n**Event flyer:** {event_flyer}'
                    submission = sub.submit(make_title(r), selftext=body, inline_media={'event_flyer': InlineImage(media=PostMedia(str(imagepath)), caption='Flyer from the event organizer')}, send_replies=False)
                except Exception as imgerr:
                    # Image posting may fail due to media processing/subreddit restrictions. Do not retry blind:
                    # Reddit may have created a post even when the response is lost.
                    print(f'Image publish uncertain for {r["title"]}: {imgerr}. Check Reddit before retrying.', file=sys.stderr)
                    raise
            else:
                submission = sub.submit(make_title(r), selftext=body, send_replies=False)
            conn.execute("UPDATE events SET status='posted', reddit_url=? WHERE key=?", ('https://www.reddit.com' + submission.permalink, r['key']))
            conn.commit()
            print('POSTED https://www.reddit.com' + submission.permalink)
        except Exception as err:
            print(f'[STOP] Reddit error: {err}', file=sys.stderr)
            print('Stopped to prevent duplicate posts. Verify the account before trying again.', file=sys.stderr)
            break
        finally:
            if imagepath:
                imagepath.unlink(missing_ok=True)


def status(args):
    conn = open_db(Path(args.db))
    for row in conn.execute('SELECT status, COUNT(*) AS n FROM events GROUP BY status'):
        print(f'{row["status"]}: {row["n"]}')
    for r in conn.execute("SELECT start,title,reddit_url FROM events WHERE status='posted' ORDER BY start DESC LIMIT 10"):
        print(r['start'], r['title'], r['reddit_url'])


def main():
    load_dotenv(ROOT / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=os.getenv('NEWARK_DB', str(ROOT / 'newark_events.db')))
    parser.add_argument('--review', default=str(ROOT / 'review.csv'))
    cmd = parser.add_subparsers(dest='command', required=True)
    d = cmd.add_parser('discover', help='Scan public sources and refresh review.csv; never posts to Reddit')
    d.add_argument('--days', type=int, default=45)
    p = cmd.add_parser('publish', help='Only posts events marked approved in review.csv')
    p.add_argument('--live', action='store_true', help='Actually post to Reddit; default is dry-run')
    p.add_argument('--from-csv', action='store_true', help='Legacy option: import approvals from review.csv; dashboard approvals are otherwise authoritative')
    p.add_argument('--limit', type=int, default=2)
    cmd.add_parser('status')
    args = parser.parse_args()
    {'discover':discover,'publish':publish,'status':status}[args.command](args)

if __name__ == '__main__':
    main()
