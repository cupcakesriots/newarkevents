"""Local, interactive moderation dashboard. No Internet access needed to review.

Start with: python dashboard.py
Open http://127.0.0.1:8765
Reddit posting is deliberately a separate, explicit CLI command.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import re
import secrets
import subprocess
import threading
import sqlite3
import sys
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from zoneinfo import ZoneInfo

from social import ensure_leads, platform_for_url, safe_url, add_lead

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('America/New_York')
CSRF = secrets.token_urlsafe(32)
SCAN_LOCK = threading.Lock()
SCAN_ACTIVE = False

# A lightweight environment loader so the dashboard works without the collector's dependencies.
def load_env(path: Path):
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if line and not line.startswith('#') and '=' in line:
            name, val = line.split('=', 1)
            if name and name not in os.environ:
                os.environ[name] = val.strip().strip('"\'')

load_env(ROOT / '.env')
DB_PATH = Path(os.getenv('NEWARK_DB', str(ROOT / 'newark_events.db')))
PASSWORD = os.getenv('DASHBOARD_PASSWORD', '')

CSS = '''
:root{color-scheme:light;--ink:#111827;--muted:#65717d;--line:#e7eaee;--bg:#f7f8fa;--blue:#155eef;--navy:#132337;--pink:#fce8d9}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}.shell{max-width:1190px;margin:0 auto;padding:0 22px}
header{background:var(--navy);color:white;padding:19px 0}header .shell{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;font-weight:790;font-size:21px;letter-spacing:-.5px}.brand .mark{background:#f6a87f;border-radius:11px;padding:5px 10px;color:#132337}
.header-links{display:flex;gap:20px;align-items:center}header a{color:#e4edff;font-weight:600;font-size:14px}main{padding-top:32px;padding-bottom:70px}
h1{font-size:32px;letter-spacing:-.9px;line-height:1.2;margin:0 0 8px}.sub{color:var(--muted);margin:0 0 23px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin:0 0 26px}
.stat{background:white;border:1px solid var(--line);border-radius:13px;padding:16px 17px;box-shadow:0 2px 4px #172b4110}.stat b{display:block;font-size:29px;line-height:1.3}.stat span{font-size:12px;color:var(--muted);font-weight:650;text-transform:uppercase;letter-spacing:.6px}
.toolbar{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center;margin:24px 0 18px}.tabs{display:flex;gap:7px;flex-wrap:wrap}.tab{padding:9px 15px;color:#344150;background:white;border:1px solid var(--line);border-radius:8px;font-size:14px;font-weight:650}.tab.sel{background:var(--navy);color:white;border-color:var(--navy)}
.btn{display:inline-block;padding:10px 15px;border-radius:8px;cursor:pointer;border:1px solid var(--line);background:white;color:#344150;font-size:13px;font-weight:750;text-align:center;line-height:1.2}.btn:hover{filter:brightness(.98);text-decoration:none}.primary{background:#175cd3;color:white;border-color:#175cd3}.dark{background:var(--navy);color:white;border-color:var(--navy)}.green{background:#117a53;color:white;border-color:#117a53}.danger{background:#fff;color:#b42318;border-color:#efd0cf}
.small{font-size:12px;color:var(--muted)}.list{display:grid;grid-template-columns:repeat(auto-fit,minmax(315px,1fr));gap:17px}.event{background:white;border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:0 3px 8px #0b1b2b0b;display:flex;flex-direction:column}
.flyer{height:180px;background:#e7edf3;display:flex;align-items:center;justify-content:center;color:#6e7b8b;overflow:hidden}.flyer img{width:100%;height:100%;object-fit:cover}.content{padding:17px;flex:1;display:flex;flex-direction:column}.content h2{font-size:19px;line-height:1.3;letter-spacing:-.3px;margin:8px 0}.meta{font-size:12px;color:#55616d;font-weight:650}.excerpt{font-size:13px;color:#44505c;margin:8px 0 12px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.links{font-size:13px;margin:auto 0 12px}.links a{margin-right:10px}.actions{display:flex;gap:7px;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:13px}.actions form{margin:0}
.status{display:inline-block;padding:4px 8px;border-radius:15px;background:#edf1f5;color:#425466;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.4px}.status.approved{background:#daf6e8;color:#087044}.status.posted{background:#e4eafe;color:#174fc1}.status.rejected,.status.dismissed{background:#fbe9e9;color:#a22a2a}.status.needs_details{background:#fff0d4;color:#9e5900}.empty{padding:48px 22px;background:white;border:1px dashed #cad0d9;border-radius:14px;text-align:center;color:var(--muted)}
.formbox{max-width:790px;background:#fff;border:1px solid var(--line);border-radius:14px;padding:25px;margin:20px 0}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:15px}label{display:block;font-weight:700;margin-bottom:5px;font-size:13px}input,select,textarea{width:100%;border:1px solid #cbd2dc;border-radius:7px;padding:11px 12px;font:inherit;background:#fff;color:#111827}textarea{min-height:105px;resize:vertical}input[type=checkbox]{width:auto;margin:0 9px 0 0}.field{margin-bottom:14px}.field-hint{font-size:12px;color:var(--muted);margin:5px 0}.check{display:flex;align-items:start;font-weight:500}.formactions{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}.error{background:#fef0ee;border:1px solid #eeb4ac;color:#9c2c22;padding:13px 16px;border-radius:9px;margin:15px 0}.notice{background:#ecf5ff;border:1px solid #d2e8fd;color:#244866;padding:13px 16px;border-radius:9px;margin:15px 0}.section-title{font-size:19px;margin:25px 0 12px}.footer{color:#76818e;font-size:12px;margin:35px 0;text-align:center}
.lead-text{white-space:pre-wrap;overflow-wrap:anywhere}.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}.mini-form{display:flex;gap:10px;align-items:end}.mini-form input{flex:1}.scanbox{display:flex;justify-content:space-between;gap:20px;align-items:center;background:white;border:1px solid var(--line);border-radius:12px;padding:16px;margin:0 0 20px}.scanbox p{margin:4px 0}.scanbox form{flex-shrink:0}.scanlog{white-space:pre-wrap;word-wrap:break-word;background:#f0f3f7;border-radius:8px;padding:16px;max-height:490px;overflow:auto;font:12px/1.55 ui-monospace,monospace}
@media(max-width:730px){.stats{grid-template-columns:repeat(2,1fr)}.formgrid,.two{grid-template-columns:1fr}.shell{padding:0 14px}.list{grid-template-columns:1fr}h1{font-size:27px}}
'''


def esc(v):
    return html.escape(str(v if v is not None else ''), quote=True)


def db():
    c = sqlite3.connect(DB_PATH, timeout=8)
    c.row_factory = sqlite3.Row
    # Needed if someone starts dashboard before running discover.
    c.execute('''CREATE TABLE IF NOT EXISTS events (
        key TEXT PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL, venue TEXT,
        address TEXT, description TEXT, event_url TEXT, original_post_url TEXT,
        image_url TEXT, source TEXT, status TEXT NOT NULL DEFAULT 'pending',
        image_approved INTEGER NOT NULL DEFAULT 0, reddit_url TEXT, created_at TEXT NOT NULL
    )''')
    ensure_leads(c)
    c.execute('''CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, log TEXT NOT NULL DEFAULT '')''')
    c.commit()
    return c



def run_discovery(scan_id: int) -> None:
    """Run collector in a background worker; a dashboard click NEVER publishes."""
    global SCAN_ACTIVE
    try:
        cp = subprocess.run([sys.executable, str(ROOT/'aggregator.py'),
                             '--db', str(DB_PATH), '--review', str(DB_PATH.parent/'review.csv'),
                             'discover', '--days', '45'], cwd=ROOT, capture_output=True,
                            text=True, timeout=900, check=False)
        combined = (cp.stdout + '\n' + cp.stderr)[-18000:]
        status = 'completed' if cp.returncode == 0 else 'failed'
    except Exception as exc:
        status, combined = 'failed', f'{type(exc).__name__}: {exc}'
    try:
        with db() as conn:
            conn.execute('UPDATE scans SET finished_at=?,status=?,log=? WHERE id=?',
                         (datetime.now(timezone.utc).isoformat(), status, combined, scan_id))
            conn.commit()
    finally:
        with SCAN_LOCK:
            SCAN_ACTIVE = False


def scan_section(conn):
    recent = conn.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 1').fetchone()
    if recent:
        info = f'Last scan: {esc(recent["started_at"][:16].replace("T", " "))} UTC · {esc(recent["status"])}'
    else:
        info = 'No scans run from the dashboard yet.'
    form = btn_post('/scan/start', 'Scan for Events', 'btn dark')
    return f'<section class="scanbox"><div><b>Discover upcoming Newark events</b><p class="small">City calendars and configured public sources. Instagram, Threads, Facebook and Nextdoor require approved API access. All discoveries go to review; no Reddit publishing.</p><p class="small">{info} · <a href="/scan">View scan results</a></p></div>{form}</section>'


def scan_page(conn):
    recent = conn.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 1').fetchone()
    if not recent:
        content = '<p>No scan has been started yet.</p>'
    else:
        content = f'<p><b>Status:</b> {esc(recent["status"])} · Started {esc(recent["started_at"])} UTC</p>'
        if recent['status'] == 'running':
            content += '<p>Source scanning can take several minutes. Refresh this page for an update.</p>'
        if recent['log']:
            content += f'<pre class="scanlog">{esc(recent["log"])}</pre>'
    return wrapper('Event scan status',f'<h1>Event discovery</h1><p class="sub">Public event sources and authorized API connections only. No posts are sent to Reddit.</p><section class="formbox">{content}<p><a class="btn" href="/">← Back to dashboard</a></p></section>')


def local_time(iso):
    try:
        d = datetime.fromisoformat(iso)
        return d.replace(tzinfo=TZ) if d.tzinfo is None else d.astimezone(TZ)
    except (TypeError, ValueError):
        return None


def date_label(iso):
    d = local_time(iso)
    return (d.strftime('%a, %b ') + str(d.day) + ' · ' + d.strftime('%I:%M %p').lstrip('0')) if d else 'Date unknown'


def event_key(title, start):
    normalized = re.sub(r'[^a-z0-9]+', '', title.casefold())
    d = local_time(start)
    value = f'{normalized}|{d.strftime("%Y-%m-%dT%H:%M") if d else ""}'
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def is_newark(address, venue):
    target = f'{address or ""} {venue or ""}'
    return bool(re.search(r'\bnewark\s*,?\s*(?:nj|new jersey|071\d{2})\b|\b071\d{2}\b', target, re.I)) and not bool(re.search(r'newark\s*,?\s*(de|oh|delaware|ohio)\b', target, re.I))


def validate_event(fields):
    d = local_time(fields.get('start', ''))
    if not fields.get('title', '').strip() or not d:
        return 'Enter an event title and a valid start date/time.'
    if not fields.get('venue', '').strip():
        return 'Enter the event venue (or an accurate location description).'
    if not is_newark(fields.get('address'), fields.get('venue')):
        return 'Please verify the address includes Newark, NJ (or a Newark 071xx ZIP code).'
    if d < datetime.now(TZ):
        return 'This event date is in the past. Only upcoming events can be queued.'
    if not safe_url(fields.get('event_url', '')):
        return 'A valid event details or ticket URL is required.'
    if fields.get('original_post_url') and not safe_url(fields['original_post_url']):
        return 'The original post URL must be a valid http(s) URL.'
    if fields.get('image_url') and not safe_url(fields['image_url']):
        return 'The image URL must be a valid http(s) URL.'
    return ''


def hidden():
    return f'<input type="hidden" name="csrf" value="{CSRF}">'


def btn_post(action, label, klass='btn', extras='', content=''):
    return f'<form method="post" action="{esc(action)}">{hidden()}{content}<button class="{klass}" type="submit">{esc(label)}</button></form>'


def wrapper(title, body, selected='events', message=''):
    note = f'<div class="notice">{esc(message)}</div>' if message else ''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} | Newark Events</title><style>{CSS}</style></head><body>
<header><div class="shell"><div class="brand"><span class="mark">N.</span> NEWARK <span style="font-weight:400;color:#b8c7da">/ Event Desk</span></div><nav class="header-links"><a href="/">Events</a><a href="/leads">Social Leads</a><a href="/events/new">+ Add Event</a></nav></div></header>
<main class="shell">{note}{body}<p class="footer">Private, review-first local dashboard · Nothing is published to Reddit from this screen.</p></main></body></html>'''


def card_image(url):
    u = safe_url(url or '')
    if u:
        return f'<div class="flyer"><img src="{esc(u)}" loading="lazy" alt="Organizer image preview" referrerpolicy="no-referrer"></div>'
    return '<div class="flyer">No event image provided</div>'


def summarize_status(conn):
    stats = {r['status']: r['n'] for r in conn.execute('SELECT status,COUNT(*) n FROM events GROUP BY status')}
    nleads = conn.execute("SELECT COUNT(*) FROM social_leads WHERE status='needs_details'").fetchone()[0]
    return ''.join(f'<div class="stat"><b>{n}</b><span>{esc(label)}</span></div>' for n, label in [
        (stats.get('pending', 0), 'Awaiting review'),
        (nleads, 'Social leads'),
        (stats.get('approved', 0), 'Approved'),
        (stats.get('posted', 0), 'Published')])


def event_card(r):
    key = quote(r['key'])
    status = r['status']
    excerpt = esc((r['description'] or '')[:310])
    links = f'<a href="{esc(r["event_url"])}" rel="noopener noreferrer" target="_blank">Details ↗</a>' if safe_url(r['event_url']) else ''
    if safe_url(r['original_post_url']) and r['original_post_url'] != r['event_url']:
        links += f'<a href="{esc(r["original_post_url"])}" rel="noopener noreferrer" target="_blank">Original ↗</a>'
    if r['reddit_url'] and safe_url(r['reddit_url']):
        links += f'<a href="{esc(r["reddit_url"])}" rel="noopener noreferrer" target="_blank">Reddit ↗</a>'
    actions = ''
    if status != 'posted':
        actions += btn_post(f'/events/{key}/decision', 'Approve', 'btn green', content='<input type="hidden" name="decision" value="approved">') if status != 'approved' else ''
        actions += f'<a class="btn" href="/events/{key}/edit">Edit details</a>'
        actions += btn_post(f'/events/{key}/decision', 'Reject', 'btn danger', content='<input type="hidden" name="decision" value="rejected">') if status != 'rejected' else ''
        actions += btn_post(f'/events/{key}/decision', 'Undo', 'btn', content='<input type="hidden" name="decision" value="pending">') if status in ('approved', 'rejected') else ''
    return f'''<article class="event">{card_image(r['image_url'])}<div class="content"><div><span class="status {esc(status)}">{esc(status)}</span> <span class="meta">{esc(r['source'])}</span></div>
<h2>{esc(r['title'])}</h2><div class="meta">{esc(date_label(r['start']))}</div><div class="meta">{esc(r['venue'] or 'Venue needs confirmation')}</div><p class="excerpt">{excerpt}</p><div class="links">{links}</div><div class="actions">{actions}</div></div></article>'''


def event_list(conn, params):
    status = params.get('status', ['pending'])[0]
    if status not in ('pending', 'approved', 'rejected', 'posted', 'all'):
        status = 'pending'
    term = (params.get('q', [''])[0])[:100]
    sql = 'SELECT * FROM events WHERE 1=1'
    values = []
    if status != 'all':
        sql += ' AND status=?'; values.append(status)
    if term:
        sql += ' AND (title LIKE ? OR venue LIKE ? OR source LIKE ?)'
        values += [f'%{term}%'] * 3
    sql += ' ORDER BY start ASC LIMIT 300'
    rows = conn.execute(sql, values).fetchall()
    tabs = ''.join(f'<a class="tab {"sel" if v == status else ""}" href="/?status={v}">{label}</a>' for v, label in [('pending','Needs review'),('approved','Approved'),('posted','Published'),('rejected','Rejected'),('all','All')])
    search = f'''<form method="get" action="/" class="mini-form"><input type="hidden" name="status" value="{esc(status)}"><input aria-label="Search events" placeholder="Search events, venues..." name="q" value="{esc(term)}"><button class="btn" type="submit">Search</button></form>'''
    cards = ''.join(event_card(r) for r in rows) or '<div class="empty">Nothing here yet. Run the collector or add an event manually.</div>'
    html_body = f'''<h1>Newark events, curated.</h1><p class="sub">Discover what's happening in Brick City. Verify the details. Approve only what belongs on r/newarkevents.</p>
<div class="stats">{summarize_status(conn)}</div>{scan_section(conn)}<div class="toolbar"><div class="tabs">{tabs}</div><a class="btn primary" href="/events/new">+ Add an event</a></div>{search}<h2 class="section-title">{len(rows)} event(s)</h2><div class="list">{cards}</div>'''
    return wrapper('Event review', html_body)


def lead_card(r):
    excerpt = (r['post_text'] or 'No caption supplied. Open the original source to check the event details.')[:500]
    buttons = ''
    if r['status'] == 'needs_details':
        buttons = f'<a class="btn primary" href="/events/new?lead={r["id"]}">Complete event details</a>'
        buttons += btn_post(f'/leads/{r["id"]}/dismiss', 'Dismiss', 'btn danger')
    original = f'<a href="{esc(r["original_post_url"])}" rel="noopener noreferrer" target="_blank">Open original post ↗</a>'
    return f'''<article class="event">{card_image(r['image_url'])}<div class="content"><div><span class="status {esc(r['status'])}">{esc(r['status'].replace('_',' '))}</span> <span class="meta">{esc(r['platform'])}</span></div>
<h2>{esc((r['post_text'] or r['platform']+' event tip')[:85])}</h2><p class="excerpt lead-text">{esc(excerpt)}</p><div class="links">{original}</div><div class="actions">{buttons}</div></div></article>'''


def leads_page(conn, params):
    status = params.get('status', ['needs_details'])[0]
    if status not in ('needs_details','converted','dismissed','all'):
        status = 'needs_details'
    sql = 'SELECT * FROM social_leads' + (' WHERE status=?' if status != 'all' else '') + ' ORDER BY discovered_at DESC LIMIT 300'
    rows = conn.execute(sql, (status,) if status != 'all' else ()).fetchall()
    tabs = ''.join(f'<a class="tab {"sel" if v == status else ""}" href="/leads?status={v}">{label}</a>' for v,label in [('needs_details','Needs details'),('converted','Added as event'),('dismissed','Dismissed'),('all','All')])
    body = f'''<h1>Social discovery inbox</h1><p class="sub">Potential Newark event announcements from Instagram, Threads, Facebook, Nextdoor or pasted links. These are leads, not verified calendar events.</p>
<div class="toolbar"><div class="tabs">{tabs}</div><a class="btn" href="/">← Back to event review</a></div>
<section class="formbox"><h2 style="margin-top:0">Paste an event post</h2><p class="small">Use this for Instagram, Threads, Facebook or Nextdoor posts the APIs cannot access. Private or login-only posts must be reviewed manually.</p>
<form method="post" action="/leads/add">{hidden()}<div class="field"><label>Original social post URL</label><input name="original_post_url" required placeholder="https://nextdoor.com/... or https://www.instagram.com/p/..."></div>
<div class="field"><label>Caption or event notes (optional)</label><textarea name="post_text" placeholder="Paste the event details you've seen, including date, time and venue if available"></textarea></div><button class="btn primary" type="submit">Add to social leads</button></form></section>
<h2 class="section-title">{len(rows)} social lead(s)</h2><div class="list">{''.join(lead_card(r) for r in rows) or '<div class="empty">No social leads yet. Paste a link above or configure Meta API access.</div>'}</div>'''
    return wrapper('Social discovery', body)


def fields_from_form(form):
    return {f: (form.get(f, [''])[0] or '').strip() for f in ('title','start','venue','address','description','event_url','original_post_url','image_url','source')}


def event_form(conn, existing=None, lead=None, error='', overrides=None):
    values = dict(existing) if existing else {}
    if lead and not existing:
        values.update({
            'title': (lead['post_text'] or '')[:105] or f"{lead['platform']} event announcement",
            'description': (lead['post_text'] or '')[:600],
            'original_post_url': lead['original_post_url'],
            'event_url': lead['event_url'] or lead['original_post_url'],
            'image_url': lead['image_url'], 'source': lead['platform'], 'start': '', 'venue': '', 'address': '',
        })
    if overrides:
        values.update(overrides)
    title = 'Edit event' if existing else 'Add a Newark event'
    action = f'/events/{quote(existing["key"])}/save' if existing else '/events/add'
    if lead and not existing:
        action += f'?lead={lead["id"]}'
    dt = local_time(values.get('start'))
    start_val = dt.strftime('%Y-%m-%dT%H:%M') if dt else ''
    textarea = f'''<div class="field"><label>Description</label><textarea name="description">{esc(values.get('description',''))}</textarea></div>'''
    inputs = [
        ('title', 'Event title', 'text', True),('start','Start date and time (Newark local time)','datetime-local',True),
        ('venue','Venue','text',True),('address','Street address, Newark, NJ','text',True),
        ('event_url','Event details / tickets link','url',True), ('original_post_url','Original announcement link','url',False),
        ('image_url','Flyer / image URL (optional)','url',False),('source','Source / organizer','text',False)
    ]
    pieces=[]
    for name, label, kind, required in inputs:
        val = start_val if name == 'start' else values.get(name,'')
        pieces.append(f'<div class="field"><label for="{name}">{esc(label)}</label><input id="{name}" name="{name}" type="{kind}" value="{esc(val)}" {"required" if required else ""}></div>')
    form = f'''<h1>{esc(title)}</h1><p class="sub">Confirm the real event date, place, organizer and links before approving for Reddit.</p>
{'<div class="error">'+esc(error)+'</div>' if error else ''}<section class="formbox"><form method="post" action="{esc(action)}">{hidden()}<div class="formgrid">{''.join(pieces[:4])}</div>{textarea}<div class="formgrid">{''.join(pieces[4:])}</div>
<div class="field"><label class="check"><input type="checkbox" name="image_approved" value="1" {"checked" if values.get("image_approved") else ""}>I confirm the organizer/rights holder permits reuploading this image to Reddit.</label>
<p class="field-hint">Otherwise, the image is linked in the Reddit post, not reuploaded.</p></div>
<div class="formactions"><button class="btn primary" type="submit">Save for review</button><a class="btn" href="/">Cancel</a></div></form></section>'''
    return wrapper(title, form)


class Handler(BaseHTTPRequestHandler):
    def _authorized(self):
        if not PASSWORD:
            return True
        auth = self.headers.get('Authorization','')
        if not auth.startswith('Basic '):
            return False
        try:
            userpass = base64.b64decode(auth[6:], validate=True).decode('utf-8')
            username, _, password = userpass.partition(':')
            return hmac.compare_digest(username, 'admin') and hmac.compare_digest(password, PASSWORD)
        except (ValueError, UnicodeError):
            return False

    def respond(self, content, status=200):
        encoded = content.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(encoded)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers(); self.wfile.write(encoded)

    def redirect(self, url):
        self.send_response(303)
        self.send_header('Location',url)
        self.send_header('Cache-Control','no-store')
        self.end_headers()

    def require_auth(self):
        if self._authorized():
            return True
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Newark Events Dashboard"')
        self.end_headers()
        return False

    def do_GET(self):
        if not self.require_auth(): return
        u = urlparse(self.path); params = parse_qs(u.query)
        conn = db()
        try:
            if u.path == '/':
                self.respond(event_list(conn, params)); return
            if u.path == '/scan':
                self.respond(scan_page(conn)); return
            if u.path == '/leads':
                self.respond(leads_page(conn, params)); return
            if u.path == '/events/new':
                lead = None
                if params.get('lead'):
                    lead = conn.execute("SELECT * FROM social_leads WHERE id=? AND status='needs_details'", (params['lead'][0],)).fetchone()
                self.respond(event_form(conn, lead=lead)); return
            match = re.fullmatch(r'/events/([0-9a-f]{20})/edit', u.path)
            if match:
                event = conn.execute('SELECT * FROM events WHERE key=?',(match.group(1),)).fetchone()
                if not event: self.respond('Event not found',404); return
                self.respond(event_form(conn, existing=event, error=params.get('error',[''])[0])); return
            self.respond('Not found',404)
        finally:
            conn.close()

    def do_POST(self):
        if not self.require_auth(): return
        try:
            size = int(self.headers.get('Content-Length','0'))
            if size <= 0 or size > 100_000:
                self.respond('Invalid form size',400); return
            data = parse_qs(self.rfile.read(size).decode('utf-8'), keep_blank_values=True)
            if not hmac.compare_digest(data.get('csrf',[''])[0], CSRF):
                self.respond('Invalid form token. Reload the page and try again.',403); return
            self.process_post(urlparse(self.path),data)
        except (UnicodeDecodeError, ValueError, sqlite3.Error) as exc:
            self.respond(wrapper('Error',f'<div class="error">{esc(exc)}</div>'),400)

    def process_post(self, u, form):
        conn = db()
        try:
            if u.path == '/scan/start':
                global SCAN_ACTIVE
                with SCAN_LOCK:
                    if SCAN_ACTIVE:
                        self.redirect('/scan'); return
                    SCAN_ACTIVE = True
                    try:
                        cursor = conn.execute('INSERT INTO scans (started_at,status) VALUES (?,?)',
                                              (datetime.now(timezone.utc).isoformat(), 'running'))
                        conn.commit()
                        thread = threading.Thread(target=run_discovery, args=(cursor.lastrowid,), daemon=True)
                        thread.start()
                    except Exception:
                        SCAN_ACTIVE = False
                        raise
                self.redirect('/scan'); return
            if u.path == '/leads/add':
                url = form.get('original_post_url',[''])[0]
                if not safe_url(url): raise ValueError('Enter a valid social post URL')
                add_lead(conn, platform_for_url(url), url, form.get('post_text',[''])[0]); self.redirect('/leads'); return
            found = re.fullmatch(r'/leads/(\d+)/dismiss', u.path)
            if found:
                conn.execute("UPDATE social_leads SET status='dismissed' WHERE id=? AND status='needs_details'", (found.group(1),)); conn.commit(); self.redirect('/leads'); return
            found = re.fullmatch(r'/events/([0-9a-f]{20})/decision', u.path)
            if found:
                key = found.group(1)
                d = form.get('decision',[''])[0]
                if d not in ('pending','approved','rejected'): raise ValueError('Unknown moderation decision')
                ev = conn.execute("SELECT * FROM events WHERE key=? AND status!='posted'", (key,)).fetchone()
                if not ev: raise ValueError('This event does not exist or was already posted')
                if d == 'approved':
                    error = validate_event(dict(ev))
                    if error:
                        self.redirect(f'/events/{key}/edit?error={quote(error)}'); return
                conn.execute('UPDATE events SET status=? WHERE key=? AND status!=?', (d,key,'posted'))
                conn.commit(); self.redirect('/?status=' + ('approved' if d == 'approved' else 'pending')); return
            is_add = u.path == '/events/add'
            found = re.fullmatch(r'/events/([0-9a-f]{20})/save', u.path)
            if is_add or found:
                fields = fields_from_form(form)
                error = validate_event(fields)
                if error:
                    # Re-render populated form to preserve curator work.
                    self.respond(event_form(conn, existing=conn.execute('SELECT * FROM events WHERE key=?',(found.group(1),)).fetchone() if found else None, error=error, overrides={**fields, 'image_approved': form.get('image_approved',[''])[0]=='1'}), 400)
                    return
                dateval = local_time(fields['start']).isoformat()
                fields['start'] = dateval
                key = event_key(fields['title'], dateval)
                rights = int(form.get('image_approved',[''])[0] == '1')
                if is_add:
                    existing = conn.execute('SELECT key FROM events WHERE key=?',(key,)).fetchone()
                    if existing: raise ValueError('Likely duplicate: this event and time already exist in the dashboard')
                    conn.execute('''INSERT INTO events (key,title,start,venue,address,description,event_url,original_post_url,image_url,source,status,image_approved,created_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',(key,fields['title'],dateval,fields['venue'],fields['address'],fields['description'],fields['event_url'],fields['original_post_url'] or fields['event_url'],fields['image_url'],fields['source'] or 'Manual', 'pending', rights,datetime.now(timezone.utc).isoformat()))
                    leadnum = parse_qs(u.query).get('lead',[''])[0]
                    if leadnum.isdigit():
                        conn.execute("UPDATE social_leads SET status='converted',event_key=? WHERE id=? AND status='needs_details'", (key,int(leadnum)))
                else:
                    oldkey=found.group(1)
                    previous=conn.execute('SELECT * FROM events WHERE key=? AND status!=?', (oldkey,'posted')).fetchone()
                    if not previous: raise ValueError('Published items cannot be edited')
                    if key != oldkey and conn.execute('SELECT 1 FROM events WHERE key=?',(key,)).fetchone():
                        raise ValueError('Changing the title/date would duplicate another known event')
                    conn.execute('''UPDATE events SET key=?,title=?,start=?,venue=?,address=?,description=?,event_url=?,original_post_url=?,image_url=?,source=?,image_approved=? WHERE key=?''',
                       (key,fields['title'],dateval,fields['venue'],fields['address'],fields['description'],fields['event_url'],fields['original_post_url'] or fields['event_url'],fields['image_url'],fields['source'] or previous['source'],rights,oldkey))
                    conn.execute('UPDATE social_leads SET event_key=? WHERE event_key=?',(key,oldkey))
                conn.commit(); self.redirect('/?status=pending' if is_add else '/?status=all'); return
            self.respond('Not found',404)
        finally:
            conn.close()


def main():
    host = os.getenv('DASHBOARD_HOST','127.0.0.1')
    if host not in ('127.0.0.1','localhost','::1') and not PASSWORD:
        sys.exit('Refusing to listen publicly without DASHBOARD_PASSWORD. Use HTTPS behind a reverse proxy for public hosting.')
    # Cloud hosts such as Railway inject PORT; keep DASHBOARD_PORT for local use.
    port = int(os.getenv('PORT') or os.getenv('DASHBOARD_PORT', '8765'))
    initial_db = db()
    initial_db.close()
    server = ThreadingHTTPServer((host,port),Handler)
    print(f'Newark Events Dashboard at http://{host}:{port} (Ctrl+C to stop)')
    if not PASSWORD: print('Local-only mode: set DASHBOARD_PASSWORD in .env before sharing this machine/service.')
    try: server.serve_forever()
    except KeyboardInterrupt: print('\nStopped.')
    finally: server.server_close()


if __name__ == '__main__': main()
