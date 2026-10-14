"""Offline coverage for the social lead workflow and dashboard validation."""
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import social
import dashboard as d

class TestSocialLeadWorkflow(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        social.ensure_leads(self.db)

    def tearDown(self):
        self.db.close()

    def test_preserves_facebook_link_identifiers_and_dedupes(self):
        original = 'https://www.facebook.com/permalink.php?story_fbid=777&id=111&utm_source=tracking'
        self.assertTrue(social.add_lead(self.db, 'Facebook', original,'Newark event workshop on Friday'))
        self.assertFalse(social.add_lead(self.db, 'Facebook', original,'repeat'))
        saved = self.db.execute('SELECT * FROM social_leads').fetchone()
        self.assertIn('story_fbid=777', saved['original_post_url'])
        self.assertNotIn('utm_source=', saved['original_post_url'])

    def test_candidate_heuristic(self):
        self.assertTrue(social.is_event_candidate('Newark arts: concert tonight'))
        self.assertFalse(social.is_event_candidate('Newark has so many nice buildings'))
        self.assertFalse(social.is_event_candidate('Free concert tonight in Trenton'))
        self.assertFalse(social.is_event_candidate('Concert tonight in Newark, Delaware'))

    def test_threads_connector_only_stores_plausible_leads(self):
        payload = {'data':[
            {'permalink':'https://www.threads.net/@organizer/post/abc','text':'Join us at the Newark NJ workshop this weekend!', 'timestamp':'2026-10-15T13:00:00Z'},
            {'permalink':'https://www.threads.net/@organizer/post/xyz','text':'Newark weather is beautiful today'}
        ]}
        with patch.object(social,'graph_get',return_value=payload) as fn:
            n=social.collect_threads(self.db,['Newark events'],'testtoken')
        self.assertEqual(n,1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM social_leads').fetchone()[0],1)
        self.assertEqual(fn.call_args[0][0],'https://graph.threads.net/keyword_search')

    def test_instagram_connector_keeps_leads_unverified(self):
        def answer(url, token, params):
            if 'ig_hashtag_search' in url:
                return {'data':[{'id':'123'}]}
            return {'data':[{'permalink':'https://www.instagram.com/p/xyz/','caption':'Join us this weekend for a newark event', 'media_url':'https://example.com/flyer.jpg'}]}
        with patch.object(social,'graph_get',side_effect=answer):
            n=social.collect_instagram(self.db,['newarkevents'],'token','user','v26.0')
        self.assertEqual(n,1)
        self.assertEqual(self.db.execute('SELECT status FROM social_leads').fetchone()[0],'needs_details')

    def test_validation_requires_newark_and_future(self):
        future=(datetime.now(ZoneInfo('America/New_York'))+timedelta(days=20)).strftime('%Y-%m-%dT19:00')
        fields={'title':'Community Celebration','start':future,'venue':'Community Center','address':'Newark, NJ 07102','event_url':'https://example.org/event','original_post_url':'','image_url':''}
        self.assertEqual(d.validate_event(fields),'')
        self.assertTrue(d.validate_event({**fields,'address':'Newark, DE'}))
        self.assertTrue(d.validate_event({**fields,'start':'not a date'}))

if __name__ == '__main__': unittest.main()
