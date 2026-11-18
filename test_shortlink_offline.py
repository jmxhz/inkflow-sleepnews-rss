import unittest
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch
import datetime as dt
import json
import sleepnews_history as h
import cloud_update as c
import hydrate_rss as hyd
import pending_queue as q
from test_sleepnews_history_offline import Response, Session

TITLE='睡前消息【2026-11-17】测试'
SHORT='https://mp.weixin.qq.com/s/abcdefghijklmnop'

def page(biz=h.BIZ, title=TITLE):
    return (f'<h1 id="activity-name">{title}</h1><a id="js_name">{h.ACCOUNT}</a>'
            f'<div id="js_content"><p>{"内容"*500}</p></div>'
            f'<script>var biz="{biz}"; var mid="123456"; var idx="1";</script>').encode()

class ShortLinkTests(unittest.TestCase):
    def test_verified_short_link_append_and_hydrate_without_refetch(self):
        session=Session([Response(body=page())])
        item=h.validate_wechat_page(session,SHORT+'?sessionkey=secret#frag',TITLE)
        self.assertEqual(item['url'],SHORT)
        channel=ET.Element('channel')
        c.append_verified(channel,item)
        node=channel.find('item')
        self.assertEqual(node.findtext('link'),SHORT)
        self.assertTrue(hyd.validate_body(node))
        root=ET.Element('rss',{'version':'2.0'}); ch=ET.SubElement(root,'channel')
        ET.SubElement(ch,'title').text=h.ACCOUNT+' · 每日新闻'; ch.append(node)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'feed.xml'; ET.ElementTree(root).write(p,encoding='utf-8',xml_declaration=True)
            fetch=Mock(side_effect=AssertionError('must not refetch verified body'))
            hyd.hydrate(p,fetch=fetch)
            fetch.assert_not_called()
            self.assertEqual(ET.parse(p).findtext('./channel/item/guid'),node.findtext('guid'))

    def test_reject_host_scheme_path_and_session_parameters_on_append(self):
        item={'name':h.ACCOUNT,'reviewId':h.ACCOUNT_ID+'_mid123456_idx1','title':TITLE,
              'date':'2026-11-17','summary':'x'*80}
        for url in ('http://mp.weixin.qq.com/s/abcdefghijklmnop',
                    'https://mp.weixin.qq.com.evil.test/s/abcdefghijklmnop',
                    'https://mp.weixin.qq.com/s/short',
                    SHORT+'?sessionkey=secret', SHORT+'#frag'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError): c.append_verified(ET.Element('channel'),dict(item,url=url))

    def test_queue_strips_session_parameters(self):
        state={'pending':{}}
        q.merge(state,[{'date':'2026-11-17','title':TITLE,'wechat_url':SHORT+'?token=secret'}],[],h.normalize)
        self.assertEqual(next(iter(state['pending'].values()))['wechat_url'],SHORT)

    def test_signed_candidate_used_only_in_memory(self):
        signed=SHORT+'?timestamp=123&signature=secret'
        discovered={'date':'2026-11-17','title':TITLE,'wechat_url':signed}
        with tempfile.TemporaryDirectory() as d:
            queue=Path(d)/'pending.json'; retry=Path(d)/'retry.json'
            verified={'date':'2026-11-17','title':TITLE,'linkKind':'stable_wechat'}
            with patch.object(h,'index_titles',return_value=[discovered]), \
                 patch.object(h,'validate_wechat_page',return_value=verified) as validate:
                found,stats=h.fetch_backfill([],s=object(),pause=0,progress_path=retry,queue_path=queue,
                    now=dt.datetime(2026,11,18,tzinfo=dt.timezone.utc))
            self.assertEqual(validate.call_args.args[1],signed)
            self.assertEqual(found,[verified])
            self.assertNotIn('secret',queue.read_text(encoding='utf-8'))
            self.assertNotIn('secret',retry.read_text(encoding='utf-8'))
            self.assertEqual(next(iter(json.loads(queue.read_text(encoding='utf-8'))['pending'].values()))['wechat_url'], SHORT)

    def test_wrong_biz_and_title_rejected(self):
        for body in (page(biz='incorrect'),page(title='睡前消息【2026-11-16】不同')):
            with self.assertRaises(ValueError): h.validate_wechat_page(Session([Response(body=body)]),SHORT,TITLE)

    def test_existing_long_links_unchanged_and_valid(self):
        root=ET.parse('feed.xml')
        urls=[item.findtext('link') for item in root.findall('./channel/item')]
        self.assertTrue(urls)
        self.assertTrue(all(h.is_wechat_url(url) for url in urls))
        short=SHORT+'?sessionkey=secret'
        self.assertEqual(h.canonical_article_url(short),SHORT)
        long='https://mp.weixin.qq.com/s?__biz=abc&mid=123456&idx=1&sn=xyz&token=secret'
        self.assertNotIn('token',h.canonical_article_url(long))

if __name__=='__main__': unittest.main()
