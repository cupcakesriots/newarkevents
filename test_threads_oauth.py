"""Offline tests for the Meta Threads authorization flow; no actual API calls."""
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as dashboard
import threads_connection as threads


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        threads.ensure_tables(self.conn)
        self.env = patch.dict(os.environ, {
            'THREADS_APP_ID': '123456',
            'THREADS_APP_SECRET': 'secret-do-not-display',
            'THREADS_REDIRECT_URI': 'https://newarkevents-production.up.railway.app/oauth/threads/callback',
            'THREADS_EXPECTED_USERNAME': 'transformthehustle',
            'THREADS_ACCESS_TOKEN': 'old-test-token',
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.conn.close()

    def test_csrf_and_scopes(self):
        url = threads.start(self.conn)
        args = parse_qs(urlparse(url).query)
        self.assertIn('threads_basic,threads_keyword_search', args['scope'])
        self.assertEqual(args['client_id'], ['123456'])
        self.assertNotIn('secret-do-not-display', url)
        self.assertEqual(args['redirect_uri'], [os.environ['THREADS_REDIRECT_URI']])
        self.assertEqual(len(args['state'][0]), 43)

    def test_state_single_use(self):
        url = threads.start(self.conn)
        state = parse_qs(urlparse(url).query)['state'][0]
        threads._consume_state(self.conn, state)
        with self.assertRaises(threads.ThreadsConnectionError):
            threads._consume_state(self.conn, state)

    def test_state_expiry(self):
        url = threads.start(self.conn)
        state = parse_qs(urlparse(url).query)['state'][0]
        self.conn.execute('UPDATE threads_oauth_states SET created_at=?', (int(time.time())-700,))
        self.conn.commit()
        with self.assertRaises(threads.ThreadsConnectionError):
            threads._consume_state(self.conn, state)

    def test_token_exchange_and_scanner(self):
        state = parse_qs(urlparse(threads.start(self.conn)).query)['state'][0]
        def get(url, **kwargs):
            if url.endswith('/access_token'):
                return FakeResponse({'access_token':'long-lived-secret', 'expires_in': 5184000})
            return FakeResponse({'id': '42', 'username':'transformthehustle'})
        with patch.object(threads.requests, 'post', return_value=FakeResponse({'access_token':'short-secret'})) as post, \
             patch.object(threads.requests, 'get', side_effect=get):
            self.assertEqual(threads.finish(self.conn, state, 'real-code'), 'transformthehustle')
        self.assertEqual(threads.token_for_scan(self.conn)[0], 'long-lived-secret')
        self.assertEqual(threads.status(self.conn)['username'], 'transformthehustle')
        # Ensure the secret never leaks to rendered HTML.
        html = dashboard.threads_connection_page(self.conn)
        self.assertNotIn('long-lived-secret', html)
        self.assertIn('@transformthehustle', html)
        self.assertEqual(post.call_args.kwargs['data']['client_id'], '123456')

    def test_disconnect_prevents_legacy_token_fallback(self):
        threads.disconnect(self.conn)
        self.assertEqual(threads.token_for_scan(self.conn)[0], '')

    def test_dashboard_navigation(self):
        html = dashboard.wrapper('test', 'body')
        self.assertIn('href="/connections/threads"', html)



class MetaLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        threads.ensure_tables(self.conn)
        dashboard.ensure_leads(self.conn)
        self.env = patch.dict(os.environ, {'THREADS_APP_SECRET': 'test-secret'}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.conn.close()

    def signed(self, user_id='42', secret='test-secret', issued_at=None):
        payload = {'algorithm':'HMAC-SHA256', 'user_id':user_id, 'issued_at':issued_at or int(time.time())}
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()
        sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
        return sig + '.' + raw

    def test_valid_signature_and_wrong_secret(self):
        self.assertEqual(threads.verify_meta_signed_request(self.signed()), '42')
        with self.assertRaises(threads.ThreadsConnectionError):
            threads.verify_meta_signed_request(self.signed(secret='wrong'))

    def test_invalid_algorithm_and_expiry(self):
        with self.assertRaises(threads.ThreadsConnectionError):
            threads.verify_meta_signed_request(self.signed(issued_at=int(time.time())-1800))

    def test_uninstall_only_own_account(self):
        self.conn.execute("INSERT INTO threads_connection (id,user_id,username,access_token,status) VALUES (1,'42','testuser','token','connected')")
        threads.handle_meta_uninstall(self.conn,'other')
        self.assertEqual(threads.status(self.conn)['status'], 'connected')
        threads.handle_meta_uninstall(self.conn,'42')
        self.assertEqual(threads.status(self.conn)['status'], 'disconnected')
        self.assertEqual(threads.token_for_scan(self.conn)[0], '')

    def test_delete_erases_threads_leads_and_returns_status(self):
        self.conn.execute("INSERT INTO threads_connection (id,user_id,username,access_token,status) VALUES (1,'42','testuser','token','connected')")
        self.conn.execute("INSERT INTO social_leads (platform,original_post_url,discovered_at) VALUES ('Threads','https://threads.net/test','2026-11-12')")
        self.conn.execute("INSERT INTO social_leads (platform,original_post_url,discovered_at) VALUES ('Instagram','https://instagram.com/test','2026-11-12')")
        response = threads.handle_meta_delete(self.conn,'42','https://example.org')
        self.assertIn('https://example.org/oauth/threads/deletion-status/', response['url'])
        self.assertEqual(threads.get_meta_deletion_status(self.conn, response['confirmation_code']), 'completed')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM social_leads WHERE platform="Threads"').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM social_leads WHERE platform="Instagram"').fetchone()[0], 1)
        self.assertEqual(threads.status(self.conn)['status'], 'disconnected')

if __name__ == '__main__':
    unittest.main()
