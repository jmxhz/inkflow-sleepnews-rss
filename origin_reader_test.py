#!/usr/bin/env python3
"""Generate a clean RSS source-link test for a reader's own fulltext extraction.

IMPORTANT: This feed does not reproduce any publisher's article text. It omits
content:encoded deliberately, unlike the existing summary-only feed, to see
whether a reader fetches the original publisher's HTML itself.
Only stable original short URLs are included; expiring Sogou signatures are not.
"""
from __future__ import annotations
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
IN=ROOT/"feed.xml"
OUT=ROOT/"origin-reader-test.xml"
DC="http://purl.org/dc/elements/1.1/"
ET.register_namespace("dc",DC)

def make():
    old=ET.parse(IN).getroot()
    if old.tag!="rss" or old.findtext("./channel/title")!="睡前消息编辑部 · 每日新闻":
        raise ValueError("Unexpected RSS input")
    root=ET.Element("rss",{"version":"2.0"})
    ch=ET.SubElement(root,"channel")
    ET.SubElement(ch,"title").text="睡前消息编辑部 · 原文提取验证"
    ET.SubElement(ch,"link").text="https://jmxhz.github.io/inkflow-sleepnews-rss/origin-reader-test.xml"
    ET.SubElement(ch,"description").text="测试 WhereMyLife 是否会从公众号原文地址自动提取完整正文；此源不含摘要或全文。"
    ET.SubElement(ch,"language").text="zh-CN"
    count=0
    for article in old.findall("./channel/item"):
        url=article.findtext("link","")
        # Only verified short links are stable; signed 'src=11' links can expire.
        if not url.startswith("https://mp.weixin.qq.com/s/"):
            continue
        title=article.findtext("title","")
        guid=article.findtext("guid","")
        if not title.startswith("睡前消息【20") or "MP_WXS_3581557283_" not in guid:
            continue
        item=ET.SubElement(ch,"item")
        ET.SubElement(item,"title").text=title
        ET.SubElement(item,"link").text=url
        ET.SubElement(item,"guid",{"isPermaLink":"false"}).text=guid+":origin-probe"
        ET.SubElement(item,"pubDate").text=article.findtext("pubDate","")
        ET.SubElement(item,f"{{{DC}}}creator").text="睡前消息编辑部"
        # No content:encoded, and a neutral description. The RSS reader must
        # fetch the original Web page to produce full text.
        ET.SubElement(item,"description").text="公众号原文抓取测试：全文请由阅读器从文章链接提取。"
        count+=1
    if not count:
        raise RuntimeError("No verified stable WeChat article URL for test feed")
    tmp=OUT.with_suffix(".tmp")
    ET.ElementTree(root).write(tmp,encoding="utf-8",xml_declaration=True)
    tmp.replace(OUT)
    print(f"origin-reader-test.xml has {count} publisher-link item(s), no copied fulltext")
    return count

if __name__=="__main__":
    make()
