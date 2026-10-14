"""Offline smoke tests; run after installing requirements: python -m unittest discover -s tests -v"""
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import tempfile
import importlib
import types
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Permit the core smoke tests to run even if optional source/API dependencies
# are not installed in this build environment. Deployments must install all requirements.
for name in ('feedparser', 'icalendar', 'recurring_ical_events'):
    try:
        importlib.import_module(name)
    except ImportError:
        stub = types.ModuleType(name)
        if name == 'icalendar':
            stub.Calendar = object
        sys.modules[name] = stub
import aggregator as a


class TestParser(unittest.TestCase):
    def test_date_and_duplication(self):
        x = a.local_dt('10/16/26 @ 8:00PM')
        self.assertEqual(x.strftime('%Y-%m-%d %H:%M'), '2026-10-16 20:00')
        ev1 = a.Event('Patti Smith', x.isoformat(), 'NJPAC', 'Newark, NJ', '', 'https://www.njpac.org/event/patti-smith/', '', '', 'NJPAC')
        ev2 = a.Event('Patti Smith', x.isoformat(), 'Prudential Hall', 'Newark, NJ', '', 'https://www.other-site.com/id/21', '', '', 'Another Source')
        self.assertEqual(a.stable_key(ev1), a.stable_key(ev2))
        ev3 = a.Event('Patti Smith', x.replace(hour=22).isoformat(), 'NJPAC', 'Newark, NJ', '', '', '', '', 'NJPAC')
        self.assertNotEqual(a.stable_key(ev1), a.stable_key(ev3))

    def test_city_filter(self):
        x = a.Event('Concert', '2026-11-01T17:00:00-04:00','Main Hall','Newark, DE','', 'https://example.com/event','','', 'test')
        self.assertFalse(a.is_newark(x))
        x.address = 'Newark, NJ 07102'
        self.assertTrue(a.is_newark(x))

    def test_njpac_fallback(self):
        s = a.BeautifulSoup('''<h1>Patti Smith</h1><meta property="og:image" content="https://example.com/flyer.jpg"><div>Dates: Fri, 10/16/26 @ 8:00PM Venue: Prudential Hall</div><a href="https://tickets.example.com/123">Buy Tickets</a>''','html.parser')
        with patch.object(a, 'soup_for', return_value=s):
            evs = a.parse_event_page('https://www.njpac.org/event/patti-smith/', {'name':'NJPAC','default_venue':'NJPAC','default_address':'1 Center St, Newark, NJ 07102'})
        self.assertEqual(len(evs),1)
        self.assertEqual(evs[0].start[:16], '2026-10-16T20:00')
        self.assertEqual(evs[0].image_url, 'https://example.com/flyer.jpg')
        self.assertIn('tickets.example.com', evs[0].event_url)

    def test_museum_date(self):
        s = a.BeautifulSoup('<h1>Dreamtime in Newark</h1><meta property="og:image" content="/flyer.png">', 'html.parser')
        with patch.object(a, 'soup_for', return_value=s):
            evs = a.parse_event_page('https://newarkmuseumart.org/event/dreamtime-in-newark/?date=202610151700', {'name':'Newark Museum of Art','default_venue':'The Newark Museum of Art','default_address':'49 Washington St, Newark, NJ'})
        self.assertEqual(len(evs),1)
        self.assertEqual(evs[0].start[:16], '2026-10-15T17:00')

    def test_review_to_dry_preview(self):
        with tempfile.TemporaryDirectory() as td:
            db = a.open_db(Path(td)/'test.db')
            ev = a.Event('Community Coffee', '2026-11-12T18:00:00-05:00', 'Café', 'Newark, NJ 07102', 'Meet your neighbors', 'https://events.example.org/register', 'https://instagram.com/p/example/', '', 'Manual')
            self.assertTrue(a.upsert(db, ev))
            self.assertFalse(a.upsert(db, ev))
            db.commit()
            queue = Path(td)/'review.csv'
            a.export_review(db, queue)
            self.assertTrue(queue.with_suffix('.html').exists())
            body = a.make_body(db.execute('SELECT * FROM events').fetchone())
            self.assertIn('Original announcement:', body)
            self.assertIn('Event details / tickets:', body)
            content = queue.read_text().replace('pending,0', 'approved,0')
            queue.write_text(content)
            a.import_review(db, queue)
            self.assertEqual(db.execute('SELECT status FROM events').fetchone()['status'], 'approved')

if __name__ == '__main__':
    unittest.main()
