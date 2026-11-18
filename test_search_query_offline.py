import unittest
from unittest.mock import patch
import sleepnews_history as h

TITLE='睡前消息〖2026-11-17〗炸大桥 送普京'

def block(title, account, href):
    return f'<li><span class="account">{account}</span><h3><a href="{href}">{title}</a></h3></li>'
class Response:
    def __init__(self, body): self.content=body.encode('utf-8')
    def raise_for_status(self): pass
class Session:
    def __init__(self, responses): self.responses=iter(responses); self.queries=[]
    def get(self, url, **kwargs):
        self.queries.append(kwargs['params']['query'])
        return Response(next(self.responses))

class QueryTests(unittest.TestCase):
    @patch.object(h.time,'sleep')
    def test_daily_query_rejects_previous_day_wrong_account_and_wrong_title(self, _sleep):
        body='<ul class="news-list">'+''.join([
            block('睡前消息【2026-11-16】炸大桥 送普京','睡前消息编辑部','/link?wrongdate'),
            block('睡前消息【2026-11-17】炸大桥 送普京','其他公众号','/link?wrongaccount'),
            block('睡前消息【2026-11-17】其他标题','睡前消息编辑部','/link?wrongtitle'),
            block(TITLE,'睡前消息编辑部','/link?correct'),
        ])+'</ul>'
        s=Session([body]); result=h.search_candidates(s,TITLE)
        self.assertTrue(result.endswith('/link?correct'))
        self.assertEqual(s.queries,['睡前消息【2026-11-17】'])
    @patch.object(h.time,'sleep')
    def test_fallbacks_keep_three_request_limit(self, _sleep):
        s=Session(['<html></html>']*3)
        self.assertIsNone(h.search_candidates(s,TITLE))
        self.assertEqual(s.queries,['睡前消息【2026-11-17】','睡前消息编辑部 炸大桥 送普京','睡前消息编辑部 2026-11-17 炸大桥 送普京'])

if __name__=='__main__': unittest.main()
