#!/usr/bin/env python3
"""GitHub-only scheduled refresh for SleepNews RSS, no user's computer.

Reads only public: jintiankansha.me headlines, Sogou WeChat search, and
the ORIGINAL WeChat article page with exact verified public-account biz.
Does not use WeRead login or any private credentials. Publishes ~240-character
excerpts and publisher links, never full copyrighted article content.

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
    ET.SubElement(node,"description").text=summary
    temporary=item.get("linkKind")=="temporary_tencent"
    label="打开微信原文（临时链接可能失效）" if temporary else "打开公众号原文"
    body="<p>"+html.escape(summary)+"…</p><p><a href=\""+html.escape(original,quote=True)+"\">"+label+"</a></p>"
    lookup=str(item.get("lookupUrl",""))
    lookup_u=hist.urlsplit(lookup)
    if temporary and lookup_u.scheme=="https" and lookup_u.hostname=="weixin.sogou.com":
        body+="<p>若原文链接失效：<a href=\""+html.escape(lookup,quote=True)+"\">按标题检索</a></p>"
    ET.SubElement(node,"{"+CONTENT+"}encoded").text=body

def refresh(days=30, max_attempts=3, dry_run=False, s=None):
    if not FEED.exists():
        raise FileNotFoundError("Missing feed.xml: refusing to create a made-up empty feed")
    tree=ET.parse(FEED)
    root=tree.getroot()
    channel, items=entries(root)
    records=current_records(items)
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
    if count:
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
            "total":len(root.findall("./channel/item")),"history":stats,
            "no_private_account_credentials":True,
            "upload_expected":count>0 or RETRY.is_file()}
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
