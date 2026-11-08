#!/usr/bin/env python3
"""Cloud-only private Kindle EPUB integrity check and optional direct delivery.

Reads the public LINK-ONLY RSS; fetches ORIGINAL WeChat articles into memory,
requires full body and original publisher/title confirmation. Never uploads,
commits, logs or exposes article bodies on GitHub Pages. The EPUB is deleted
on exit. Only sends when explicitly called with --send AND all email secrets
are configured. No appended "open original" footer/link appears in articles.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from hashlib import sha256
from html import escape
from io import BytesIO
from pathlib import Path
import re
import os
import ssl
import smtplib
import tempfile
from urllib.parse import urlsplit
import zipfile
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from PIL import Image
from ebooklib import epub
import requests

from sleepnews_history import ACCOUNT, BIZ, UA, normalize

ROOT=Path(__file__).resolve().parent
FEED=ROOT/"feed.xml"
ENCODED="{http://purl.org/rss/1.0/modules/content/}encoded"
BIZ_PATTERN=re.compile(r'''\b(?:var|let|const)\s+biz\s*=\s*["']([^"']+)["']''')
MAX_IMAGE_BYTES=3_000_000
MAX_IMAGES=16
CST=timezone(timedelta(hours=8))

def load_feed(path=FEED,limit=3):
    root=ET.parse(path).getroot()
    if root.tag!="rss" or root.get("version")!="2.0" or root.findtext("./channel/title")!=ACCOUNT+" · 每日新闻":
        raise ValueError("不是已核实的睡前消息 RSS")
    selected=[]
    for item in root.findall("./channel/item"):
        title=item.findtext("title","")
        link=item.findtext("link","")
        if item.find(ENCODED) is not None or item.find("description") is not None:
            raise ValueError("正式 RSS 意外包含可能被误认成全文的内容")
        parts=urlsplit(link)
        if parts.scheme!="https" or parts.hostname!="mp.weixin.qq.com" or parts.path not in ("/s",) and not parts.path.startswith("/s/"):
            continue
        if not title.startswith("睡前消息【20"):
            continue
        selected.append((title,link))
        if len(selected)>=limit:break
    if not selected:
        raise ValueError("RSS 没有可用微信原文链接")
    return selected

def fetch_original(session,title,url):
    response=session.get(url,timeout=(9,30),allow_redirects=False,
                         headers={"User-Agent":UA,"Accept-Language":"zh-CN,zh;q=0.9"})
    if response.status_code!=200 or len(response.content)>6_000_000:
        raise RuntimeError("微信原文访问失败："+str(response.status_code))
    response.encoding="utf-8"
    soup=BeautifulSoup(response.text,"lxml")
    name=soup.select_one("#js_name")
    headline=soup.select_one("#activity-name")
    content=soup.select_one("#js_content")
    biz=BIZ_PATTERN.search(response.text)
    if not name or not headline or not content or not biz:
        raise RuntimeError("微信未提供完整原文结构；不能使用摘要替代")
    if name.get_text(" ",strip=True)!=ACCOUNT or biz.group(1)!=BIZ:
        raise RuntimeError("公众号标识不一致")
    if normalize(headline.get_text(" ",strip=True))!=normalize(title):
        raise RuntimeError("文章标题不一致")
    whole=content.get_text(" ",strip=True)
    if len(whole)<800:
        raise RuntimeError("正文不足800字，拒绝视为完整文章")
    return content,whole

def prepare_chapter(session, title, link, root_element, whole, book, ix):
    soup=BeautifulSoup(str(root_element),"lxml")
    element=soup.select_one("#js_content") or soup.body or soup
    # Remove active media, publisher interface and non-reading elements.
    for bad in list(element.select("script,style,iframe,video,audio,form,button,svg,canvas,noscript")):
        bad.decompose()
    image_items=[]
    for index,img in enumerate(list(element.select("img"))):
        if index>=MAX_IMAGES:
            img.decompose()
            continue
        raw=img.get("data-src") or img.get("data-original") or img.get("src") or ""
        u=urlsplit(raw)
        if u.scheme not in ("https","http") or u.hostname not in ("mmbiz.qpic.cn","mmbiz.qlogo.cn","wx.qlogo.cn"):
            img.decompose()
            continue
        try:
            r=session.get(raw,headers={"Referer":"https://mp.weixin.qq.com/"},
                          timeout=(7,16),stream=True)
            if r.status_code!=200:
                raise RuntimeError("图片服务器未返回有效内容")
            buf=BytesIO()
            for part in r.iter_content(32768):
                buf.write(part)
                if buf.tell()>MAX_IMAGE_BYTES:
                    raise RuntimeError("单张图片过大")
            with Image.open(BytesIO(buf.getvalue())) as image:
                image.thumbnail((1500,1900))
                image=image.convert("RGB")
                target=BytesIO()
                image.save(target,format="JPEG",quality=76)
                dest=f"images/{ix}_{index}.jpg"
                book.add_item(epub.EpubItem(uid=f"image-{ix}-{index}",file_name=dest,
                                           media_type="image/jpeg",content=target.getvalue()))
                img.attrs={"src":dest,"alt":img.get("alt","")}
                image_items.append(dest)
        except (requests.RequestException,OSError,ValueError,RuntimeError):
            img.decompose()
    # External links in original article become plain visible text. The book
    # never adds its own source/footer/advertisement links.
    for a in list(element.find_all("a")):
        a.unwrap()
    for node in list(element.find_all(True)):
        if node.name not in {"h1","h2","h3","h4","h5","h6","p","div","section","span","strong","b","i","em",
                             "blockquote","br","hr","ul","ol","li","pre","code","table","thead","tbody",
                             "tr","td","th","img","sup","sub"}:
            node.unwrap()
        elif node.name!="img":
            node.attrs={}
    content_html="".join(str(child) for child in element.contents)
    text=BeautifulSoup(content_html,"lxml").get_text(" ",strip=True)
    if len(text)<max(800,int(len(whole)*.9)):
        raise RuntimeError("文章清洗后缺失正文段落")
    if not text[:65].replace(" ","")[:40] in whole.replace(" ",""):
        raise RuntimeError("文章正文开头没有通过一致性检查")
    chapter=epub.EpubHtml(title=title,file_name=f"chapter_{ix}.xhtml",lang="zh")
    # Convert browser HTML into XML-compliant EPUB XHTML via BeautifulSoup XML
    # serializer by passing clean body fragment; ebooklib normalizes structure.
    chapter.content="<html xmlns='http://www.w3.org/1999/xhtml'><head><title>"+escape(title)+"</title></head><body><h2>"+escape(title)+"</h2>"+content_html+"</body></html>"
    book.add_item(chapter)
    return chapter,len(text),len(image_items)

def make_private_epub(feed,output,max_items=3,strict_all=False):
    pairs=load_feed(feed,max_items)
    net=requests.Session()
    net.headers.update({"User-Agent":UA})
    book=epub.EpubBook()
    book.set_identifier("urn:inkflow:sleepnews:"+datetime.now(CST).date().isoformat())
    book.set_title("睡前消息编辑部 · 每日新闻")
    book.set_language("zh")
    chapters=[];stats=[];fails=[]
    for index,(title,url) in enumerate(pairs):
        try:
            content,whole=fetch_original(net,title,url)
            chapter,text_count,image_count=prepare_chapter(net,title,url,content,whole,book,index)
            chapters.append(chapter)
            stats.append((len(whole),text_count,image_count))
        except (requests.RequestException,ValueError,OSError,RuntimeError) as exc:
            fails.append((title,type(exc).__name__,str(exc)[:90]))
    if not chapters or strict_all and len(chapters)!=len(pairs):
        raise RuntimeError("云端无法完整提取所有测试文章；"+str(fails)[:350])
    book.toc=chapters
    book.spine=["nav",*chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(output),book)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("生成的 EPUB ZIP 损坏")
        contents=[archive.read(name).decode("utf-8","replace")
                  for name in archive.namelist() if name.endswith(".xhtml") and "chapter_" in name]
        if len(contents)!=len(chapters):
            raise RuntimeError("生成的 EPUB 章节数与全文采集数不匹配")
        if any("文章原址" in ch or "打开公众号原文" in ch or "按标题检索" in ch for ch in contents):
            raise RuntimeError("EPUB 仍然包含自动添加的原文链接")
        if any(len(BeautifulSoup(ch,"xml").get_text(" ",strip=True))<800 for ch in contents):
            raise RuntimeError("EPUB 里至少一篇正文短于 800 字")
    return {"articles":len(chapters),"failed":len(fails),
            "source_text_chars":sum(s[0] for s in stats),
            "epub_text_chars":sum(s[1] for s in stats),
            "images":sum(s[2] for s in stats),
            "bytes":output.stat().st_size,"failures":fails}

def send_email(path):
    names=("KINDLE_SMTP_HOST","KINDLE_SMTP_USERNAME","KINDLE_SMTP_PASSWORD",
           "KINDLE_SMTP_FROM","KINDLE_SMTP_TO")
    missing=[x for x in names if not os.getenv(x)]
    if missing:raise RuntimeError("未配置 Kindle 私人 SMTP："+",".join(missing))
    host=os.environ["KINDLE_SMTP_HOST"]
    port=int(os.getenv("KINDLE_SMTP_PORT","465"))
    if not 1<=port<=65535:raise ValueError("SMTP port out of range")
    to=os.environ["KINDLE_SMTP_TO"]
    msg=EmailMessage()
    msg["From"]=os.environ["KINDLE_SMTP_FROM"]
    msg["To"]=to
    msg["Subject"]="睡前消息编辑部·私人全文合辑"
    msg.set_content("私人阅读合辑，未对外公开文章内容。")
    msg.add_attachment(path.read_bytes(),maintype="application",subtype="epub+zip",filename=path.name)
    with smtplib.SMTP_SSL(host,port,context=ssl.create_default_context(),timeout=40) as server:
        server.login(os.environ["KINDLE_SMTP_USERNAME"],os.environ["KINDLE_SMTP_PASSWORD"])
        server.send_message(msg)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--feed",type=Path,default=FEED)
    ap.add_argument("--sample",type=int,default=2)
    ap.add_argument("--send",action="store_true")
    ap.add_argument("--strict",action="store_true")
    args=ap.parse_args()
    if not 1<=args.sample<=20:ap.error("sample out of range")
    with tempfile.TemporaryDirectory(prefix="sleepnews-private-epub-") as td:
        path=Path(td)/"sleepnews-private.epub"
        result=make_private_epub(args.feed,path,args.sample,args.strict)
        if args.send:
            send_email(path)
        # Do not log any email addresses, personal credentials, source HTML,
        # private contents, or attach generated EPUB to a public workflow run.
        print("PRIVATE_EPUB_VALIDATION",result)
        print("SMTP_STATUS","accepted" if args.send else "not_sent")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
