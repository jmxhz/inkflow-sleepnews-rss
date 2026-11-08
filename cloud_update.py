#!/usr/bin/env python3
"""GitHub-only scheduled refresh for SleepNews RSS, no user's computer.

Reads only public: jintiankansha.me headlines, Sogou WeChat search, and
the ORIGINAL WeChat article page with exact verified public-account biz.
Does not use WeRead login or any private credentials. Adds verified article
identities and stable URLs; the scheduled post-update hydration step must
supply the full original body before the feed is committed.

Run in public jmxhz/inkflow-sleepnews-rss GitHub Actions environment.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import sleepnews_history as hist

ROOT=Path(__file__).resolve().parent
FEED=ROOT/"feed.xml"
RETRY=ROOT/"retry-state.json"
ACCOUNT="睡前消息编辑部"
ID="MP_WXS_3581557283"
CONTENT="http://purl.org/rss/1.0/modules/content/"
DC="http://purl.org/dc/elements/1.1/"
ATOM="http://www.w3.org/2005/Atom"
ET.register_namespace("content",CONTENT)
ET.register_namespace("dc",DC)
ET.register_namespace("atom",ATOM)
CST=dt.timezone(dt.timedelta(hours=8))

def entries(root):
    ch=root.find("channel")
    if root.tag!="rss" or root.get("version")!="2.0" or ch is None:
        raise ValueError("RSS XML structure invalid; refusing to modify")
    if ch.findtext("title")!=ACCOUNT+" · 每日新闻":
        raise ValueError("RSS is not the expected SleepNews publisher")
    return ch, ch.findall("item")

def current_records(items):
    rows=[]
    for item in items:
        title=item.findtext("title","")
        guid=item.findtext("guid","")
        try:
            day=hist.date_in_title(title)
        except ValueError:
            raise ValueError("Existing RSS contains unexpected title")
        if not guid.startswith("urn:inkflow:sleepnews:"+ID+"_"):
            raise ValueError("Existing RSS item came from a different source")
        rows.append({"title":title,"date":day.isoformat()})
    return rows

def append_verified(channel,item):
    if item.get("name")!=ACCOUNT:
        raise ValueError("Publisher does not match")
    if not str(item.get("reviewId","")).startswith(ID+"_"):
        raise ValueError("WeChat account ID mismatch")
    day=hist.date_in_title(item["title"])
    if day.isoformat()!=item.get("date"):
        raise ValueError("Title and date differ")
    original=str(item["url"])
    u=hist.urlsplit(original)
    if u.scheme!="https" or u.hostname!="mp.weixin.qq.com" or u.path!="/s":
        raise ValueError("Only original WeChat URLs may be published")
    summary=str(item["summary"])[:240]
    if len(summary)<70:
        raise ValueError("Not enough verified text for RSS")
    node=ET.SubElement(channel,"item")
    ET.SubElement(node,"title").text=item["title"]
    ET.SubElement(node,"link").text=original
    ET.SubElement(node,"guid",{"isPermaLink":"false"}).text="urn:inkflow:sleepnews:"+item["reviewId"]
    ET.SubElement(node,"pubDate").text=dt.datetime.combine(
        day,dt.time(12,0),tzinfo=CST).strftime("%a, %d %b %Y %H:%M:%S %z")
    ET.SubElement(node,"{"+DC+"}creator").text=ACCOUNT
    # The mandatory hydration step adds full content:encoded + description.
    # Never write a misleading 240-character excerpt into these fields.

def remove_truncated_entries(channel):
    """Remove only invalid or truncated body fields, retain verified fulltext.

    A previously published long article must not be deleted during the next
    scheduled run. Any invalid or partial body will be re-fetched by the
    mandatory hydrate_rss.py step before publication.
    """
    changed=0
    for item in channel.findall("item"):
        content=item.findtext("{"+CONTENT+"}encoded")
        description=item.findtext("description")
        if content is None and description is None:
            continue
        if (content and description and content==description and
            len(BeautifulSoup(content,"lxml").get_text(" ",strip=True))>=800 and
            not any(x in content for x in ("文章原址","打开公众号原文","按标题检索"))):
            continue
        for child in list(item):
            if child.tag in ("description","{"+CONTENT+"}encoded"):
                item.remove(child)
        changed+=1
    return changed


def refresh(days=30, max_attempts=3, dry_run=False, s=None):
    if not FEED.exists():
        raise FileNotFoundError("Missing feed.xml: refusing to create a made-up empty feed")
    tree=ET.parse(FEED)
    root=tree.getroot()
    channel, items=entries(root)
    records=current_records(items)
    upgraded=remove_truncated_entries(channel)
    candidates,stats=hist.fetch_backfill(
        records,days=days,max_attempts=max_attempts,
        progress_path=RETRY,s=s)
    existing={(x["date"],hist.normalize(x["title"])) for x in records}
    count=0
    for candidate in candidates:
        key=(candidate["date"],hist.normalize(candidate["title"]))
        if key in existing: continue
        append_verified(channel,candidate)
        existing.add(key)
        count+=1
    if count or upgraded:
        all_items=channel.findall("item")
        for node in all_items: channel.remove(node)
        # Sort entirely by article-day title so feed readers see newest first.
        all_items.sort(key=lambda item:(
            hist.date_in_title(item.findtext("title","")).isoformat(),
            item.findtext("guid","")),reverse=True)
        for item in all_items[:90]:channel.append(item)
        tmp=FEED.with_suffix(".tmp")
        ET.ElementTree(root).write(tmp,encoding="utf-8",xml_declaration=True)
        tmp.replace(FEED)
    result={"ok":True,"existing":len(items),"added":count,
            "upgraded_link_only":upgraded,
            "total":len(root.findall("./channel/item")),"history":stats,
            "no_private_account_credentials":True,
            "upload_expected":count>0 or upgraded>0 or RETRY.is_file()}
    print(json.dumps(result,ensure_ascii=False))
    if not stats["index"]:
        raise RuntimeError("Historical index returned no publisher articles; cloud refresh needs checking")
    return result

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--days",type=int,default=30)
    parser.add_argument("--attempts",type=int,default=3)
    args=parser.parse_args()
    if not 1<=args.days<=60 or not 0<=args.attempts<=10:
        parser.error("Invalid scan limits")
    try:
        refresh(days=args.days,max_attempts=args.attempts)
    except Exception as exc:
        print("CLOUD_REFRESH_ERROR:",str(exc)[:240],file=sys.stderr)
        raise SystemExit(2)
