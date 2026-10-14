"""Transient Eventbrite link previews using Brave's authorized Web Search API.

DOES NOT persist, cache, export or index search results. Only a single
requested lookup is kept in memory for the immediate HTTP response.
Users must independently verify each event on its source website before
manually entering it in the editorial event review queue.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from html.parser import HTMLParser
from html import escape as escape_html, unescape as unescape_html
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



class _SafeSearchMarkup(HTMLParser):
    """Keep the simple emphasis in Brave snippets without trusting their HTML."""
    SAFE_TAGS = {'b': 'strong', 'strong': 'strong', 'i': 'em', 'em': 'em'}
    SKIP = {'script', 'style', 'iframe', 'svg'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.open_tags = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.suppressed += 1
        elif not self.suppressed:
            clean_tag = self.SAFE_TAGS.get(tag)
            if clean_tag:
                self.parts.append(f'<{clean_tag}>')
                self.open_tags.append(clean_tag)
            elif tag == 'br':
                self.parts.append(' ')

    def handle_startendtag(self, tag, attrs):
        if not self.suppressed and tag == 'br':
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.suppressed = max(self.suppressed - 1, 0)
        elif not self.suppressed:
            clean_tag = self.SAFE_TAGS.get(tag)
            if clean_tag and clean_tag in self.open_tags:
                # Close mismatched input as well as properly paired emphasis.
                while self.open_tags:
                    last = self.open_tags.pop()
                    self.parts.append(f'</{last}>')
                    if last == clean_tag:
                        break
            elif tag in ('p', 'div', 'li'):
                self.parts.append(' ')

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(escape_html(data, quote=True))

    def finish(self):
        while self.open_tags:
            self.parts.append(f'</{self.open_tags.pop()}>')
        return ''.join(self.parts).strip()


def safe_search_markup(raw):
    """Return safe HTML with bold/italics displayed rather than raw HTML code."""
    parser = _SafeSearchMarkup()
    value = str(raw or '')[:5000]
    # Some indexes send escaped formatting tags (e.g. &lt;strong&gt;).
    # Decode once, then permit only the tags above; never trust raw HTML.
    if '&lt;' in value or '&#60;' in value or '&#x3c;' in value.lower():
        value = unescape_html(value)
    parser.feed(value)
    return parser.finish()


def plain_search_text(raw):
    """Text-only version, for filtering and checking event dates."""
    # Brave sometimes provides markup that is malformed; HTMLParser is tolerant.
    class TextReader(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.fragments = []
            self.ignore = 0
        def handle_starttag(self, tag, attrs):
            if tag in ('script', 'style', 'iframe', 'svg'): self.ignore += 1
        def handle_endtag(self, tag):
            if tag in ('script', 'style', 'iframe', 'svg'): self.ignore = max(0, self.ignore-1)
        def handle_data(self, data):
            if not self.ignore: self.fragments.append(data)
    parser = TextReader()
    parser.feed(str(raw or '')[:5000])
    return re.sub(r'\s+', ' ', unescape_html(' '.join(parser.fragments))).strip()


def brave_preview_image(hit):
    """Use only Brave's transient image proxy, when a non-logo thumbnail exists."""
    thumb = hit.get('thumbnail')
    if not isinstance(thumb, dict) or thumb.get('logo'):
        return ''
    src = thumb.get('src')
    if not isinstance(src, str):
        return ''
    try:
        parsed = urlparse(src)
        if parsed.scheme == 'https' and parsed.hostname == 'imgs.search.brave.com' and not parsed.username and not parsed.password:
            return src[:2000]
    except ValueError:
        pass
    return ''


_MONTH_NAMES = ('january','february','march','april','may','june','july','august',
                'september','october','november','december')
_MONTHS = {name: i+1 for i, name in enumerate(_MONTH_NAMES)}
_MONTHS.update({name[:3]: i+1 for i, name in enumerate(_MONTH_NAMES)})
_EVENT_DATE = re.compile(r'\b(?P<month>' + '|'.join(_MONTHS) + r')\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>20\d{2}))?\b', re.I)
_TITLE_YEAR = re.compile(r'(?<!\d)(20\d{2})(?!\d)')
_SAME_MONTH_RANGE = re.compile(
    r'\b(?P<month>' + '|'.join(_MONTHS) + r')\.?\s+'
    r'(?P<start_day>\d{1,2})\s*(?:-|–|—|to)\s*'
    r'(?P<end_day>\d{1,2})(?:,?\s+(?P<year>20\d{2}))?\b', re.I)


def title_date_signal(raw_title, raw_snippet="", today=None):
    """Conservatively filter dates in result *titles*; never assume snippet dates are events.

    Returns (definitely_past, indicative_date_string). If the year is unclear,
    do not filter. A date range uses the final date mentioned in the title.
    """
    today = today or datetime.now(ZoneInfo('America/New_York')).date()
    title = plain_search_text(raw_title)
    snippet = plain_search_text(raw_snippet)
    # The title is strong evidence of an event date; a matching date in the
    # snippet may resolve a missing year. Never infer from unrelated dates.
    snippet_dates = {}
    for sm in _EVENT_DATE.finditer(snippet):
        if sm.group('year'):
            snippet_dates[(_MONTHS[sm.group('month').lower()[:3]], int(sm.group('day')))] = int(sm.group('year'))
    explicit_years = [int(x) for x in _TITLE_YEAR.findall(title)]
    unique_year = explicit_years[0] if len(set(explicit_years)) == 1 else None
    known_dates = []
    for match in _EVENT_DATE.finditer(title):
        month = _MONTHS[match.group('month').lower()[:3]]
        year = int(match.group('year')) if match.group('year') else (unique_year or snippet_dates.get((month, int(match.group('day')))))
        if year is None: continue
        try: known_dates.append(date(year, month, int(match.group('day'))))
        except ValueError: continue
    # Some event titles abbreviate a multi-day range: "Oct 8–18, 2026".
    # Include the end date so an event that's still active isn't hidden.
    for rng in _SAME_MONTH_RANGE.finditer(title):
        year = int(rng.group('year')) if rng.group('year') else unique_year
        if year is not None:
            try:
                known_dates.append(date(year, _MONTHS[rng.group('month').lower()[:3]], int(rng.group('end_day'))))
            except ValueError:
                pass
    if not known_dates:
        return False, ''
    last = max(known_dates)
    return last < today, f'{last.strftime("%b")} {last.day}, {last.year}'


def indexed_date_warning(raw_title, raw_snippet, today=None):
    """Flag old indexed snippet dates without declaring recurring events expired.

    Unlike dates in titles, a date in a snippet is weak evidence and should
    *never* determine whether a result is removed entirely.
    """
    today = today or datetime.now(ZoneInfo('America/New_York')).date()
    # An unexpired date explicitly in the title is stronger than a random
    # date in the search snippet, so do not flag it as old.
    expired, explicit_date = title_date_signal(raw_title, raw_snippet, today)
    if explicit_date and not expired:
        return ''
    snippet = plain_search_text(raw_snippet)
    matches = []
    for match in _EVENT_DATE.finditer(snippet):
        # Only dates with an explicit year are safe enough to flag. We don't
        # invent a year from a general title such as "Tickets 2026".
        if not match.group('year'):
            continue
        month = _MONTHS[match.group('month').lower()[:3]]
        try:
            matches.append(date(int(match.group('year')), month, int(match.group('day'))))
        except ValueError:
            continue
    if matches and max(matches) < today:
        observed = max(matches)
        return (f'Search snippet mentions {observed.strftime("%b")} {observed.day}, '
                f'{observed.year}, which is past. Future dates not verified.')
    return ''

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
            params={'q': make_query(mode),'count':20,'country':'US','search_lang':'en','result_filter':'web','text_decorations':False},
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
        evidence = f'{plain_search_text(title)} {plain_search_text(excerpt)}'
        if not NEWARK_HINT.search(evidence) or OTHER_NEWARK.search(evidence):
            continue
        # A search index may return old event pages; only reject when a
        # past event date is unambiguously mentioned in the result title.
        expired, date_hint = title_date_signal(title, excerpt)
        if expired:
            continue
        seen.add(url)
        results.append({
            'url': url,
            'title': plain_search_text(title)[:180] or 'Eventbrite event link',
            'snippet': excerpt[:500],  # sanitized by the dashboard renderer
            'thumbnail': brave_preview_image(hit),
            'date_hint': date_hint,
            'date_warning': indexed_date_warning(title, excerpt),
        })
    # Promote results with a readable date, and put older indexed snippet
    # mentions last. Never claim that ambiguous/multiple-date results expired.
    results.sort(key=lambda item: 2 if item['date_warning'] else (0 if item['date_hint'] else 1))
    # No global, database, session, file, or job-store writes.
    return results, ''
