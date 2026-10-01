"""Offline API-contract tests, not a claim of Firefox live acceptance."""
from datetime import datetime, timedelta, timezone
import unittest
from kk.tools.windows.browser import BrowserTabs


class TabCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tabs = [dict(id=i, windowId=10 if i < 4 else 20, index=i if i < 4 else 0,
                         title='Neutral %s' % i, active=i in {0, 4},
                         pinned=i == 0, discarded=i == 3, url='https://example.invalid/private')
                     for i in range(5)]
        self.queries = []

    def query(self, query):
        self.queries.append(query.copy())
        rows = [r.copy() for r in self.tabs if not query.get('windowId') or r['windowId'] == query['windowId']]
        if query.get('currentWindow'):
            rows = [r for r in rows if r['windowId'] == 10]
        return dict(tabs=rows, query=query, complete=True, private_windows_access=True,
                    timestamp=datetime.now(timezone.utc).isoformat())

    def test_four_tabs_switch_close_order_and_two_windows(self):
        adapter = BrowserTabs(self.query, policy_allowed=True)
        first = adapter.list('current_window')
        self.assertEqual(first['completeness'], 'complete')
        self.assertEqual([r['tabId'] for r in first['items']], [0, 1, 2, 3])
        self.assertEqual(self.queries[-1], {'currentWindow': True})
        for row in self.tabs[:4]:
            row['active'] = row['id'] == 2
        second = adapter.list('window', window_id=10)
        self.assertEqual([r['tabId'] for r in second['items'] if r['active']], [2])
        self.tabs.pop(1)
        for i, row in enumerate(self.tabs[:3]):
            row['index'] = i
        final = adapter.list()
        self.assertEqual(self.queries[-1], {})
        self.assertEqual([r['tabId'] for r in final['items']], [0, 2, 3, 4])
        self.assertEqual(final['completeness'], 'complete')
        self.assertTrue(final['items'][0]['pinned'])
        self.assertTrue(final['items'][2]['discarded'])
        self.assertNotIn('url', final['items'][0])

    def test_blocked_never_calls_bridge(self):
        result = BrowserTabs(self.query).list()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['completeness'], 'unknown')
        self.assertEqual(self.queries, [])

    def test_active_filter_and_missing_permission_cannot_claim_complete(self):
        def incomplete(query):
            packet = self.query(query)
            packet['query'] = {'active': True}
            packet['tabs'] = [packet['tabs'][0]]
            packet['tabs'][0].pop('title')
            return packet
        result = BrowserTabs(incomplete, policy_allowed=True).list()
        self.assertEqual(result['completeness'], 'partial')
        self.assertFalse(result['verified'])

    def test_missing_index_and_private_access_are_partial(self):
        def incomplete(query):
            packet = self.query(query)
            packet['tabs'].pop(1)
            packet['private_windows_access'] = False
            return packet
        result = BrowserTabs(incomplete, policy_allowed=True).list()
        self.assertEqual(result['completeness'], 'partial')
        self.assertGreaterEqual(len(result['limitations']), 2)

    def test_old_snapshot_is_not_fresh(self):
        def stale(query):
            packet = self.query(query)
            packet['timestamp'] = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
            return packet
        result = BrowserTabs(stale, policy_allowed=True).list()
        self.assertEqual(result['completeness'], 'unknown')
        self.assertFalse(result['items'])

    def test_no_connected_adapter_is_unknown_not_empty(self):
        result = BrowserTabs(policy_allowed=True).list()
        self.assertEqual(result['completeness'], 'unknown')
        self.assertEqual(result['status'], 'UNVERIFIED')

    def test_global_duplicate_id_across_windows_is_partial(self):
        self.tabs[-1]['id'] = 0
        result = BrowserTabs(self.query, policy_allowed=True).list()
        self.assertEqual(result['completeness'], 'partial')
        self.assertFalse(result['verified'])

    def test_empty_current_window_is_not_a_verified_window(self):
        self.tabs.clear()
        result = BrowserTabs(self.query, policy_allowed=True).list('current_window')
        self.assertEqual(result['completeness'], 'partial')

    def test_negative_native_ids_or_index_are_unverified(self):
        for field in ('id', 'windowId', 'index'):
            with self.subTest(field=field):
                original = self.tabs[0][field]
                self.tabs[0][field] = -1
                result = BrowserTabs(self.query, policy_allowed=True).list()
                self.assertEqual(result['status'], 'UNVERIFIED')
                self.assertFalse(result['items'])
                self.tabs[0][field] = original


if __name__ == '__main__':
    unittest.main()
