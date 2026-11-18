import datetime as dt
import tempfile
import unittest
from pathlib import Path
import pending_queue as q
import check_freshness
from unittest.mock import patch
import requests
import sleepnews_history as history

class QueueTests(unittest.TestCase):
    def test_retains_old_unpublished_and_fairly_rotates(self):
        now = dt.datetime(2026, 11, 18, tzinfo=dt.timezone.utc)
        norm = str.casefold
        state = {'pending': {}}
        old = {'date': '2026-09-01', 'title': 'Old'}
        new = {'date': '2026-11-18', 'title': 'New'}
        q.merge(state, [old, new], [], norm)
        self.assertEqual(q.select(state, now, 1)[0]['title'], 'New')
        q.attempted(state, new, now, norm, 'unresolved')
        self.assertEqual(q.select(state, now, 1)[0]['title'], 'Old')
        q.merge(state, [], [], norm)
        self.assertEqual(len(state['pending']), 2)
        q.merge(state, [], [new], norm)
        self.assertEqual(q.select(state, now, 1)[0]['title'], 'Old')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'pending.json'; q.save(p, state)
            self.assertEqual(q.load(p), state)

    def test_full_feed_excludes_old_backlog_without_discarding(self):
        now = dt.datetime(2026, 11, 18, tzinfo=dt.timezone.utc)
        state = {'pending': {}}
        old = {'date': '2026-06-01', 'title': 'old'}
        new = {'date': '2026-11-18', 'title': 'new'}
        q.merge(state, [old, new], [], str.casefold)
        self.assertEqual(q.select(state, now, 3, newer_than='2026-08-01')[0]['title'], 'new')
        self.assertEqual(len(state['pending']), 2)

    def test_index_outage_still_retries_durable_backlog(self):
        now = dt.datetime(2026, 11, 18, tzinfo=dt.timezone.utc)
        item = {'date': '2026-09-01', 'title': 'Old'}
        with tempfile.TemporaryDirectory() as d:
            queue = Path(d)/'queue.json'
            state = {'pending': {}}
            q.merge(state, [item], [], history.normalize); q.save(queue, state)
            with patch.object(history, 'index_titles', side_effect=requests.ConnectionError()), \
                 patch.object(history, 'search_candidates', return_value=None):
                entries, stats = history.fetch_backfill([], max_attempts=1, pause=0,
                    progress_path=Path(d)/'retry.json', queue_path=queue, now=now, s=object())
            self.assertEqual(entries, [])
            self.assertTrue(stats['external_source_unavailable'])
            self.assertEqual(stats['attempted'], 1)
            self.assertEqual(stats['unresolved'], 1)
            self.assertEqual(len(q.load(queue)['pending']), 1)

    def test_atomic_replace_retries_windows_sharing_violation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'state.json'
            real_replace = Path.replace
            calls = []
            def flaky(source, target):
                calls.append(1)
                if len(calls) == 1:
                    raise PermissionError('sharing violation')
                return real_replace(source, target)
            with patch.object(Path, 'replace', flaky), patch.object(q.time, 'sleep'):
                q.save(path, {'pending': {}})
            self.assertEqual(len(calls), 2)
            self.assertEqual(q.load(path), {'pending': {}})

    def test_freshness_uses_explicit_published_feed(self):
        with tempfile.TemporaryDirectory() as d:
            published = Path(d)/'published.xml'
            published.write_text('<rss><channel><item><pubDate>Wed, 11 Nov 2026 12:00:00 +0800</pubDate></item></channel></rss>')
            worktree = Path(d)/'feed.xml'
            worktree.write_text('<rss><channel><item><pubDate>Wed, 18 Nov 2026 12:00:00 +0800</pubDate></item></channel></rss>')
            now = dt.datetime(2026,11,18,14,tzinfo=dt.timezone.utc)
            self.assertTrue(check_freshness.check(published, Path(d)/'missing', now))
            self.assertFalse(check_freshness.check(worktree, Path(d)/'missing', now))

    def test_stale_feed_alert(self):
        with tempfile.TemporaryDirectory() as d:
            feed = Path(d)/'feed.xml'
            feed.write_text('<rss><channel><item><pubDate>Wed, 11 Nov 2026 12:00:00 +0800</pubDate></item></channel></rss>')
            warnings = check_freshness.check(feed, Path(d)/'missing', dt.datetime(2026,11,18,tzinfo=dt.timezone.utc))
            self.assertTrue(any('older than' in x for x in warnings))

if __name__ == '__main__': unittest.main()
