import unittest
from datetime import date
from unittest.mock import patch
import sleepnews_history as h

class Response:
    def __init__(self, status=200, body=b'', headers=None):
        self.status_code=status; self.content=body; self.headers=headers or {}
        self.text=body.decode('utf-8')
    def raise_for_status(self):
        if self.status_code != 200: raise RuntimeError('status')
class Session:
    def __init__(self, responses): self.responses=list(responses); self.calls=[]
    def get(self, url, **kwargs): self.calls.append(url); return self.responses.pop(0)

def article(day):
    return ('<div class="cell item"><span class="item_title"><span class="hide-content">'
        f'睡前消息【2026-11-{day}】标题</span></span><a class="node">睡前消息编辑部</a></div>').encode()

class HistoryTests(unittest.TestCase):
    def test_empty_first_page_candidates_do_not_stop_next_page(self):
        s=Session([Response(body=article('01')),Response(body=article('17')),Response(body=b'')])
        found=h.index_titles(s,days=3,today=date(2026,11,18))
        self.assertEqual([x['date'] for x in found],[]) # old page is definitively past cutoff
    def test_future_first_page_does_not_stop_next(self):
        s=Session([Response(body=article('20')),Response(body=article('17')),Response(body=b'')])
        self.assertEqual(h.index_titles(s,days=3,today=date(2026,11,18))[0]['date'],'2026-11-17')
    def test_http_diagnostic_is_distinct(self):
        with self.assertRaisesRegex(ValueError,'HTTP 状态 403'):
            h.validate_wechat_page(Session([Response(status=403)]),'https://mp.weixin.qq.com/s?x=1','x')
    def test_redirect_rejects_untrusted_without_fetch(self):
        s=Session([Response(status=302,headers={'Location':'https://example.org/'})])
        with self.assertRaisesRegex(ValueError,'重定向目标'):
            h.validate_wechat_page(s,'https://mp.weixin.qq.com/s?x=1','x')
        self.assertEqual(len(s.calls),1)
    def test_allowed_redirect_still_requires_identity_checks(self):
        s=Session([Response(status=302,headers={'Location':'/s?x=2'}),Response(body=b'<html></html>')])
        with self.assertRaisesRegex(ValueError,'完整原文结构'):
            h.validate_wechat_page(s,'https://mp.weixin.qq.com/s?x=1','x')
        self.assertEqual(len(s.calls),2)
    def test_oversize_distinct(self):
        with self.assertRaisesRegex(ValueError,'大小上限'):
            h.validate_wechat_page(Session([Response(body=b'x'*6000001)]),'https://mp.weixin.qq.com/s?x=1','x')

if __name__=='__main__': unittest.main()
