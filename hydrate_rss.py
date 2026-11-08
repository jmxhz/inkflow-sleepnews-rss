#!/usr/bin/env python3
"""Hydrate existing SleepNews RSS items with verified full publisher body.

Preserves item GUID, title, original URL, publication date and ordering. Does
not modify any existing complete body. Re-fetches only link-only or previously
truncated items; fails closed if any fails validation, never publishes a partial
daily publication. Intended for GitHub Actions before commit.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
import argparse
import json
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import requests

from fulltext_private import sanitize_wechat_content
from private_fulltext import fetch_original, UA
from sleepnews_history import ACCOUNT, ACCOUNT_ID, normalize

ROOT=Path(__file__).resolve().parent
FEED=ROOT/"feed.xml"
CONTENT="{http://purl.org/rss/1.0/modules/content/}encoded"
GUID_PREFIX="urn:inkflow:sleepnews:"+ACCOUNT_ID+"_"

def validate_body(item):
    """Return True only when description and content both contain full HTML."""
    a=item.findtext(CONTENT)
    b=item.findtext("description")
    if a is None and b is None:
        return False
    if not a or not b or a!=b:
        return False
    body=BeautifulSoup(a,"lxml").get_text(" ",strip=True)
    if len(body)<800:
        return False
    if any(x in a for x in ("文章原址","打开公众号原文","按标题检索")):
        return False
    return True

def strip_invalid_xml_characters(text):
    """Preserve all valid Unicode; XML 1.0 forbids some invisible controls."""
    return "".join(ch for ch in text if
                   ch in "\t\n\r" or
                   0x20<=ord(ch)<=0xD7FF or
                   0xE000<=ord(ch)<=0xFFFD or
                   0x10000<=ord(ch)<=0x10FFFF)


def hydrate(path=FEED, session=None, fetch=fetch_original, minimum=800):
    path=Path(path)
    tree=ET.parse(path)
    root=tree.getroot()
    if (root.tag!="rss" or root.get("version")!="2.0" or
        root.findtext("./channel/title")!=ACCOUNT+" · 每日新闻"):
        raise ValueError("Unexpected feed identity")
    items=root.findall("./channel/item")
    if not items:
        raise ValueError("Empty feed, refusing to overwrite")
    seen_ids=set()
    seen_titles=set()
    incomplete=[]
    for node in items:
        guid=node.findtext("guid","")
        title=node.findtext("title","")
        link=node.findtext("link","")
        parsed=urlsplit(link)
        if not guid.startswith(GUID_PREFIX):
            raise ValueError("Unexpected article GUID")
        if parsed.scheme!="https" or parsed.hostname!="mp.weixin.qq.com" or (
            parsed.path!="/s" and not parsed.path.startswith("/s/")):
            raise ValueError("Unexpected article original URL")
        key=normalize(title)
        if guid in seen_ids or key in seen_titles:
            raise ValueError("Duplicate publisher article")
        seen_ids.add(guid)
        seen_titles.add(key)
        if not validate_body(node):
            incomplete.append(node)
    net=session or requests.Session()
    net.headers.update({"User-Agent":UA,"Accept-Language":"zh-CN,zh;q=0.9"})
    hydrated=0
    total_new_chars=0
    images=0
    for node in incomplete:
        title=node.findtext("title","")
        original=node.findtext("link","")
        content,unfiltered=fetch(net,title,original)
        body,readable=sanitize_wechat_content(content)
        body=strip_invalid_xml_characters(body)
        readable=BeautifulSoup(body,"lxml").get_text(" ",strip=True)
        if len(readable)<minimum or len(readable)<int(len(unfiltered)*.96):
            raise RuntimeError("Full article verification failed; RSS left unchanged: "+title)
        # Edit only safe source-derived body fields. Never append a source URL.
        for field in ("description",CONTENT):
            existing=node.find(field)
            if existing is not None:
                node.remove(existing)
        ET.SubElement(node,CONTENT).text=body
        ET.SubElement(node,"description").text=body
        hydrated+=1
        total_new_chars+=len(readable)
        images+=body.count("<img ")
    if any(not validate_body(node) for node in items):
        raise RuntimeError("At least one article has no complete body; no publication")
    if hydrated:
        tmp=path.with_name(path.name+".tmp")
        tree.write(tmp,encoding="utf-8",xml_declaration=True)
        # Never publish an unparseable file even when the source contained
        # HTML control characters that XML 1.0 cannot represent.
        try:
            ET.parse(tmp)
        except ET.ParseError:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(path)
    result={"ok":True,"total_items":len(items),"hydrated":hydrated,
            "new_fulltext_characters":total_new_chars,"new_image_tags":images,
            "missing_fulltext":0}
    print("HYDRATE_RSS_RESULT",json.dumps(result,ensure_ascii=False))
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--feed",type=Path,default=FEED)
    args=parser.parse_args()
    hydrate(args.feed)

if __name__=="__main__":
    main()
