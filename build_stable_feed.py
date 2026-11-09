#!/usr/bin/env python3
"""Build a separate, stable-link fulltext RSS and permanent reading pages.

The legacy feed.xml is NOT modified, because WhereMyLife cached its old
temporary WeChat URLs; changes to legacy <link> create duplicate WML entries.
The stable feed uses permanent GitHub Pages links for full articles and serves
images locally instead of depending on WeChat's expiring signatures.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
import json
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from PIL import Image
import requests

ROOT=Path(__file__).resolve().parent
BASE="https://jmxhz.github.io/inkflow-sleepnews-rss"
LEGACY=ROOT/"feed.xml"
DEST=ROOT/"feed-stable.xml"
ARTICLE_ROOT=ROOT/"stable"
IMAGE_ROOT=ROOT/"assets"
CONTENT="{http://purl.org/rss/1.0/modules/content/}encoded"
VALID_IMAGE_HOSTS={"mmbiz.qpic.cn","mmbiz.qlogo.cn","wx.qlogo.cn"}
ET.register_namespace("content","http://purl.org/rss/1.0/modules/content/")
ET.register_namespace("dc","http://purl.org/dc/elements/1.1/")
MAX_IMAGE_BYTES=6_000_000
MAX_IMAGE_PIXELS=18_000_000
Image.MAX_IMAGE_PIXELS=MAX_IMAGE_PIXELS

def stable_path(guid):
    if not guid.startswith("urn:inkflow:sleepnews:MP_WXS_3581557283_"):
        raise ValueError("Unrecognized article GUID")
    return "stable/"+sha256(guid.encode("utf-8")).hexdigest()[:28]+".html"

def local_image_path(url):
    parsed=urlsplit(url)
    if parsed.scheme!="https" or parsed.hostname not in VALID_IMAGE_HOSTS:
        raise ValueError("Unexpected image source")
    return "assets/"+sha256(url.encode("utf8")).hexdigest()[:28]+".jpg"

def cached_image(source, destination, session):
    """Fetch and compress only once; reject fake HTML/captcha image responses."""
    if destination.exists():
        if destination.stat().st_size>800 and destination.read_bytes()[:2]==b"\xff\xd8":
            return
        raise ValueError("Existing stored image corrupt: "+destination.name)
    response=session.get(source,stream=True,timeout=(8,24),
                         headers={"Referer":"https://mp.weixin.qq.com/"})
    if response.status_code!=200:
        raise RuntimeError("Publisher image server rejected an image")
    data=BytesIO()
    try:
        for part in response.iter_content(32768):
            data.write(part)
            if data.tell()>MAX_IMAGE_BYTES:raise RuntimeError("Original picture is too large")
    finally:
        response.close()
    with Image.open(BytesIO(data.getvalue())) as picture:
        picture.load()
        picture.thumbnail((1600,2000))
        if picture.width*picture.height>MAX_IMAGE_PIXELS:
            raise RuntimeError("Image oversized")
        rgba=picture.convert("RGBA")
        bg=Image.new("RGB",rgba.size,(255,255,255))
        bg.paste(rgba,mask=rgba.getchannel("A"))
        out=BytesIO()
        bg.save(out,format="JPEG",quality=81,optimize=True)
        binary=out.getvalue()
        if len(binary)<500:raise RuntimeError("Invalid compressed image")
        destination.parent.mkdir(exist_ok=True,parents=True)
        tmp=destination.with_suffix(".tmp")
        tmp.write_bytes(binary)
        tmp.replace(destination)

def prepare_body(source_html,session,mirror_images=True):
    soup=BeautifulSoup(source_html,"lxml")
    area=soup.body or soup
    count=0
    for img in area.select("img"):
        url=img.get("src","")
        path=local_image_path(url)
        if mirror_images:
            cached_image(url,ROOT/path,session)
        img["src"]=BASE+"/"+path
        count+=1
    text="".join(str(x) for x in area.contents)
    return text,count

def html_page(title,pub_date,body):
    """Full reading page without appended original-source links."""
    return (
        "<!doctype html>\n"
        "<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"robots\" content=\"index,follow\">"
        "<title>"+escape(title)+"</title>"
        "<style>"
        "body{max-width:760px;margin:2rem auto;padding:0 1rem;"
        "font:18px/1.85 -apple-system,BlinkMacSystemFont,'Noto Sans SC',sans-serif;"
        "color:#202124;background:#fff}h1{font-size:1.5rem;line-height:1.45}"
        ".meta{color:#777;font-size:0.78rem;margin-bottom:2rem}"
        "img{display:block;max-width:100%;height:auto;margin:1.2rem auto}"
        "table{display:block;overflow-x:auto;max-width:100%}"
        "@media(prefers-color-scheme:dark){body{color:#e8eaed;background:#17191b}"
        ".meta{color:#aeb4ba}}</style></head><body>"
        "<article><h1>"+escape(title)+"</h1>"
        "<div class=\"meta\">睡前消息编辑部 · "+escape(pub_date)+"</div>"
        +body+"</article></body></html>"
    )

def build(input_path=LEGACY,output_path=DEST,session=None,mirror_images=True):
    input_path=Path(input_path);output_path=Path(output_path)
    source_tree=ET.parse(input_path)
    root=source_tree.getroot()
    if root.findtext("./channel/title")!="睡前消息编辑部 · 每日新闻":
        raise ValueError("Unexpected source feed")
    channel=root.find("channel")
    entries=channel.findall("item")
    if not entries:raise ValueError("No articles")
    client=session or requests.Session()
    client.headers.update({"User-Agent":"Mozilla/5.0","Accept-Language":"zh-CN,zh;q=0.9"})
    result=deepcopy(root)
    new_channel=result.find("channel")
    new_channel.find("title").text="睡前消息编辑部 · 每日新闻（稳定全文）"
    new_channel.find("link").text=BASE+"/feed-stable.xml"
    new_channel.find("description").text="每日新闻完整正文与图片；文章永久阅读页由 GitHub Pages 托管，不依赖过期微信原文链接。"
    seen=set()
    count=0
    char_total=0
    image_count=0
    for item in new_channel.findall("item"):
        title=item.findtext("title","")
        guid=item.findtext("guid","")
        pub_date=item.findtext("pubDate","")
        original_body=item.findtext(CONTENT)
        description=item.findtext("description")
        if guid in seen:raise RuntimeError("Duplicate source GUID")
        seen.add(guid)
        if not original_body or description!=original_body:
            raise RuntimeError("Missing or mismatched full HTML body")
        text=BeautifulSoup(original_body,"lxml").get_text(" ",strip=True)
        if len(text)<800:raise RuntimeError("Article text truncated")
        body,images=prepare_body(original_body,client,mirror_images=mirror_images)
        if len(BeautifulSoup(body,"lxml").get_text(" ",strip=True))!=len(text):
            raise RuntimeError("Image rewriting lost article text")
        page=stable_path(guid)
        if mirror_images:
            f=ROOT/page
            f.parent.mkdir(parents=True,exist_ok=True)
            html=html_page(title,pub_date,body)
            if not f.exists() or f.read_text(encoding="utf8")!=html:
                f.write_text(html,encoding="utf8")
        item.find("link").text=BASE+"/"+page
        item.find(CONTENT).text=body
        item.find("description").text=body
        count+=1
        char_total+=len(text)
        image_count+=images
    if count!=len(entries):
        raise RuntimeError("Unexpected article count")
    if mirror_images:
        output_path.parent.mkdir(parents=True,exist_ok=True)
        tmp=output_path.with_suffix(".tmp")
        ET.ElementTree(result).write(tmp,encoding="utf-8",xml_declaration=True)
        ET.parse(tmp)
        tmp.replace(output_path)
    result={"ok":True,"items":count,"chars":char_total,
            "image_references":image_count,"stable_pages":count}
    print("STABLE_FEED_RESULT",json.dumps(result,ensure_ascii=False))
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,default=LEGACY)
    p.add_argument("--target",type=Path,default=DEST)
    args=p.parse_args()
    build(args.source,args.target)

if __name__=="__main__":
    main()
