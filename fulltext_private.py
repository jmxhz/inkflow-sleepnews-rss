#!/usr/bin/env python3
"""Generate a PRIVATE, full-body RSS2 fixture for WhereMyLife compatibility.

This deliberately does not publish to GitHub Pages or any public URL.
A configured private access-controlled delivery service must be supplied
before this may be used with WhereMyLife; it contains copyrighted articles.
No original article footnote or publisher-site link is appended to the body.
"""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import requests

from private_fulltext import FEED, fetch_original, load_feed, UA, ACCOUNT

CONTENT="http://purl.org/rss/1.0/modules/content/"
DC="http://purl.org/dc/elements/1.1/"
ET.register_namespace("content",CONTENT)
ET.register_namespace("dc",DC)
VALID_IMAGES={"mmbiz.qpic.cn","mmbiz.qlogo.cn","wx.qlogo.cn"}
ELEMENTS={"h1","h2","h3","h4","h5","h6","p","div","span","section","strong","em","b","i",
          "blockquote","br","hr","ul","ol","li","pre","code","table","thead","tbody",
          "tr","td","th","img","sup","sub"}
NO_COPY={"script","style","iframe","video","audio","form","button","svg","canvas","noscript"}

def sanitize_wechat_content(content):
    doc=BeautifulSoup(str(content),"lxml")
    area=doc.select_one("#js_content") or doc.body
    if area is None:raise ValueError("No readable WeChat body")
    for bad in list(area.find_all(NO_COPY)):bad.decompose()
    for img in list(area.find_all("img")):
        src=img.get("data-src") or img.get("data-original") or img.get("src") or ""
        parsed=urlsplit(src)
        if parsed.scheme not in ("https","http") or parsed.hostname not in VALID_IMAGES:
            img.decompose()
        else:
            img.attrs={"src":src,"alt":img.get("alt","")}
    for link in list(area.find_all("a")):
        # Keep anchor text (if any) but eliminate URLs in article body.
        link.unwrap()
    for el in list(area.find_all(True)):
        if el.name not in ELEMENTS:
            el.unwrap()
        elif el.name!="img":
            el.attrs={}
    html="".join(str(el) for el in area.contents)
    return html,BeautifulSoup(html,"lxml").get_text(" ",strip=True)

def create_private_fulltext_rss(base=FEED,out=None,limit=2,minimum=800):
    out=Path(out)
    # The project might be launched from another working directory.
    public_root=Path(__file__).resolve().parent
    if out.resolve().is_relative_to(public_root):
        raise ValueError("Do not write article bodies into public Github repository")
    old=ET.parse(base)
    oldroot=old.getroot()
    if oldroot.findtext("./channel/title")!=ACCOUNT+" · 每日新闻":
        raise ValueError("Unexpected original RSS account")
    src_items=oldroot.findall("./channel/item")
    selected={title:link for title,link in load_feed(base,limit=limit)}
    r=ET.Element("rss",{"version":"2.0"})
    ch=ET.SubElement(r,"channel")
    for field in ["title","link","language","generator"]:
        text=oldroot.findtext("./channel/"+field) or ""
        if text:ET.SubElement(ch,field).text=text
    ET.SubElement(ch,"description").text="Private full-length articles for authorized personal reading"
    net=requests.Session()
    net.headers["User-Agent"]=UA
    count=0
    totals=[]
    for item in src_items:
        title=item.findtext("title","")
        link=item.findtext("link","")
        if title not in selected or selected[title]!=link:continue
        content,unfiltered=fetch_original(net,title,link)
        body,readable=sanitize_wechat_content(content)
        if len(readable)<minimum or len(readable)<int(len(unfiltered)*0.96):
            raise RuntimeError("Article text incomplete; stop. "+title)
        uid=item.findtext("guid","")
        if not uid.startswith("urn:inkflow:sleepnews:"):raise ValueError("Unexpected identifier")
        entry=ET.SubElement(ch,"item")
        for field in ("title","link","guid","pubDate"):
            elem=item.find(field)
            if elem is not None:
                node=ET.SubElement(entry,field,dict(elem.attrib))
                node.text=elem.text
        ET.SubElement(entry,"{"+DC+"}creator").text=ACCOUNT
        ET.SubElement(entry,"{"+CONTENT+"}encoded").text=body
        # Some older newsletter aggregators use <description> only.
        # Set BOTH to full article, not a misleading 240-char snippet.
        ET.SubElement(entry,"description").text=body
        count+=1
        totals.append(len(readable))
    if count!=len(selected):raise RuntimeError("Not all requested articles exported")
    out.parent.mkdir(parents=True,exist_ok=True)
    # Private output only, not tracked in the published GitHub repository.
    tmp=out.with_suffix(".tmp")
    ET.ElementTree(r).write(tmp,encoding="utf-8",xml_declaration=True)
    tmp.replace(out)
    return {"count":count,"total_text_chars":sum(totals),"article_characters":totals,"bytes":out.stat().st_size,"path":str(out)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--limit",type=int,default=2)
    p.add_argument("--source",type=Path,default=FEED)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    if not 1<=args.limit<=20:p.error("invalid limit")
    result=create_private_fulltext_rss(args.source,args.output,args.limit)
    # No body contents in logs.
    print({k:v for k,v in result.items() if k!="article_characters"})
if __name__=="__main__":main()
