#!/usr/bin/env python3
"""Recover durable original WeChat article links from verified previous-article links.

Runs on GitHub Actions at most once per China-local date. Never publishes
article bodies or images. Can repair expiring Sogou redirect signatures when
the publisher linked a stable canonical URL in another validated article.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone,timedelta
import html
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit,parse_qs
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import requests
from sleepnews_history import normalize,BIZ,ACCOUNT,ACCOUNT_ID,UA

ROOT=Path(__file__).resolve().parent
FEED=ROOT/"feed.xml"
STATE=ROOT/"link-recovery-state.json"
CST=timezone(timedelta(hours=8))
PREVIOUS=re.compile(r"\bpre_article_link\s*:\s*'([^']*)'")
BIZ_VAR=re.compile(r'\b(?:var|let|const)\s+biz\s*=\s*["\x27]([^"\x27]+)["\x27]')

def canonical_previous(markup):
    m=PREVIOUS.search(markup)
    if not m or not m.group(1):
        return ""
    url=m.group(1).replace(r"\x26","&").replace(r"\u0026","&")
    for _ in range(3):url=html.unescape(url)
    url=url.replace("http://mp.weixin.qq.com","https://mp.weixin.qq.com",1)
    split=urlsplit(url)
    params=parse_qs(split.query)
    if (split.scheme!="https" or split.hostname!="mp.weixin.qq.com" or
        split.path!="/s" or len(url)>1000 or
        not all(params.get(k) for k in ("__biz","mid","idx","sn")) or
        params["__biz"][0]!=BIZ or
        not re.fullmatch(r"[0-9a-fA-F]{32}",params["sn"][0])):
        return ""
    return url

def verified_body(page):
    soup=BeautifulSoup(page,"lxml")
    title=soup.select_one("#activity-name")
    publisher=soup.select_one("#js_name")
    body=soup.select_one("#js_content")
    if not title or not publisher or not body:
        return ""
    if publisher.get_text(" ",strip=True)!=ACCOUNT:
        return ""
    if len(body.get_text(" ",strip=True))<300:
        return ""
    biz=BIZ_VAR.search(page)
    if not biz or biz.group(1)!=BIZ:
        return ""
    return title.get_text(" ",strip=True)

def recover(feed=FEED,state_path=STATE,limit=12,force=False,session=None,delay=.4):
    feed=Path(feed)
    state_path=Path(state_path)
    today=datetime.now(CST).date().isoformat()
    if state_path.exists() and not force:
        try:
            state=json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("last_checked")==today:
                print(json.dumps({"ok":True,"skipped":"already_checked_today"},ensure_ascii=False))
                return 0
        except (ValueError,OSError):
            pass
    tree=ET.parse(feed)
    root=tree.getroot()
    if root.tag!="rss" or root.findtext("./channel/title")!="睡前消息编辑部 · 每日新闻":
        raise ValueError("This RSS is not the expected verified publisher")
    items=root.findall("./channel/item")
    lookup={}
    for item in items:
        guid=item.findtext("guid","")
        if not guid.startswith("urn:inkflow:sleepnews:"+ACCOUNT_ID+"_"):
            raise ValueError("Unexpected RSS source GUID")
        title=item.findtext("title","")
        lookup[normalize(title)]=item
    session=session or requests.Session()
    session.headers.update({"User-Agent":UA,"Accept-Language":"zh-CN,zh;q=0.9"})
    checked=0
    improved=0
    found=set()
    for source in items[:limit]:
        url=source.findtext("link","")
        parsed=urlsplit(url)
        if parsed.scheme!="https" or parsed.hostname!="mp.weixin.qq.com":
            continue
        try:
            resp=session.get(url,timeout=(7,22),allow_redirects=False)
            checked+=1
            if resp.status_code!=200 or len(resp.content)>6_000_000:
                continue
            source_title=verified_body(resp.text)
            if normalize(source_title)!=normalize(source.findtext("title","")):
                continue
            previous=canonical_previous(resp.text)
            if not previous or previous in found:
                continue
            found.add(previous)
            earlier=session.get(previous,timeout=(7,22),allow_redirects=False)
            if earlier.status_code!=200 or len(earlier.content)>6_000_000:
                continue
            title=verified_body(earlier.text)
            target=lookup.get(normalize(title))
            if target is None:
                continue
            old_url=target.findtext("link","")
            if old_url.startswith("https://mp.weixin.qq.com/s/"):
                continue
            target.find("link").text=previous
            improved+=1
            print("Stable original publisher link repaired:",title)
        except requests.RequestException:
            pass
        time.sleep(max(0,delay))
    if improved:
        tmp=feed.with_suffix(".tmp")
        tree.write(tmp,encoding="utf-8",xml_declaration=True)
        tmp.replace(feed)
    state_path.write_text(json.dumps({"last_checked":today,"checked":checked,"repaired":improved},ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"ok":True,"checked":checked,"repaired":improved,"total_items":len(items)},ensure_ascii=False))
    return improved

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--max-sources",type=int,default=12)
    parser.add_argument("--force",action="store_true")
    args=parser.parse_args()
    if not 1<=args.max_sources<=20:
        parser.error("max-sources out of range")
    recover(limit=args.max_sources,force=args.force)
