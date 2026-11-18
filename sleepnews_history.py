"""Bounded, verified historical discovery for SleepNews using public indexes.

Index titles are ONLY candidates. An article is accepted only after fetching
its real mp.weixin.qq.com page and verifying account name, fixed biz, exact
daily title/date, and substantial text. Does not bypass challenges or paywalls.
Search engine source links can expire; retain stable title-search fallback.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs, quote
import hashlib
import html
import json
import re
import time
import unicodedata

from bs4 import BeautifulSoup
import requests

ROOT=Path(__file__).resolve().parent
PROGRESS=ROOT/"data"/"sleepnews-history-progress.json"
INDEX="https://www.jintiankansha.me/column/XJrN20GONA"
SOGOU="https://weixin.sogou.com/weixin"
BIZ="MzU4MTU1NzI4Mw=="
ACCOUNT="睡前消息编辑部"
ACCOUNT_ID="MP_WXS_3581557283"
TITLE=re.compile(r"^睡前消息\s*[【〖\[]\s*(20\d{2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[】〗\]]")
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def normalize(value):
    return "".join(c for c in unicodedata.normalize("NFKC",str(value)).casefold()
                   if c.isalnum())

def date_in_title(title):
    match=TITLE.search(title)
    if not match:
        raise ValueError("文章不属于按日编排的睡前新闻")
    return date(*(int(x) for x in match.groups()))

def session():
    s=requests.Session()
    s.headers.update({"User-Agent":UA,"Accept-Language":"zh-CN,zh;q=0.9",
                      "Referer":"https://weixin.sogou.com/"})
    return s

def index_titles(s, days=30, today=None, max_pages=3):
    from datetime import timezone
    today=today or datetime.now(timezone(timedelta(hours=8))).date()
    earliest=today-timedelta(days=days)
    candidates={}
    for page in range(1,max_pages+1):
        response=s.get(INDEX,params=({"page":page} if page>1 else None),
                       timeout=(7,18))
        response.raise_for_status()
        soup=BeautifulSoup(response.content,"lxml")
        count=0
        page_dates=[]
        for block in soup.select(".cell.item"):
            title_el=block.select_one(".item_title .hide-content")
            publisher=block.select_one("a.node")
            if not title_el or not publisher or publisher.get_text(" ",strip=True)!=ACCOUNT:
                continue
            title=title_el.get_text(" ",strip=True)
            try: d=date_in_title(title)
            except ValueError:continue
            count+=1
            page_dates.append(d)
            if earliest<=d<=today+timedelta(days=1):
                candidate={"date":d.isoformat(),"title":title}
                # Only a direct official WeChat URL is a candidate, never a
                # third-party article, subscription page, or fabricated link.
                for anchor in block.select("a[href]"):
                    href=urljoin(INDEX,anchor["href"])
                    if is_wechat_url(href):
                        candidate["wechat_url"]=href
                        break
                candidates[d.isoformat()]=candidate
        if not count:break
        if page_dates and all(d<earliest for d in page_dates):
            break
    return sorted(candidates.values(),key=lambda x:x["date"],reverse=True)

def search_candidates(s,title):
    datepart=date_in_title(title).isoformat()
    tail=TITLE.sub("",title,count=1).strip()
    tokens=re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]+",tail)
    words=" ".join(tokens[:5])[:65]
    short=" ".join(tokens[:2])[:38]
    queries=[ACCOUNT+" "+short,ACCOUNT+" "+words,
             ACCOUNT+" "+datepart+" "+words[:25]]
    queries=list(dict.fromkeys(queries))
    for query in queries:
        response=s.get(SOGOU,params={"type":"2","query":query},timeout=(7,20))
        response.raise_for_status()
        soup=BeautifulSoup(response.content,"lxml")
        for block in soup.select("ul.news-list li"):
            publisher=block.select_one(".s-p,.account")
            anchor=block.select_one("h3 a[href]")
            if not publisher or publisher.get_text(" ",strip=True)!=ACCOUNT or not anchor:
                continue
            found=anchor.get_text(" ",strip=True)
            try:
                if date_in_title(found)!=date_in_title(title) or normalize(found)!=normalize(title):
                    continue
            except ValueError:
                continue
            href=urljoin(SOGOU,anchor["href"])
            u=urlsplit(href)
            if u.scheme=="https" and u.hostname=="weixin.sogou.com" and u.path=="/link":
                return href
        time.sleep(1.0)
    return None

def sogou_to_tencent(s, href):
    u=urlsplit(href)
    if u.hostname!="weixin.sogou.com" or u.path!="/link" or u.scheme!="https":
        raise ValueError("无法确认搜狗中转页面的地址")
    r=s.get(href,timeout=(8,18),allow_redirects=False)
    if r.status_code!=200:
        raise ValueError("搜狗文章中转页不可用")
    segments=re.findall(r"url\s*\+=\s*'([^']*)'\s*;",r.text)
    if not 5<=len(segments)<=32:
        raise ValueError("搜狗未返回标准微信原文中转链接")
    link="".join(segments)
    u=urlsplit(link)
    if u.scheme!="https" or u.hostname!="mp.weixin.qq.com" or u.path!="/s":
        raise ValueError("搜狗中转目标不是微信原文")
    if len(link)>750:
        raise ValueError("微信原文地址异常")
    return link

def stable_lookup(title):
    return SOGOU+"?"+urlencode({"type":"2","query":ACCOUNT+" "+title})

def is_wechat_url(link):
    u=urlsplit(link)
    try:
        return (len(link)<=750 and u.scheme=="https" and
                u.hostname=="mp.weixin.qq.com" and u.port in (None,443) and
                not u.username and not u.password and
                (u.path=="/s" or bool(re.fullmatch(r"/s/[A-Za-z0-9_-]{14,100}",u.path))))
    except ValueError:
        return False


def validate_wechat_page(s, link,expected):
    if not is_wechat_url(link):
        raise ValueError("原文网址不属于微信")
    # Follow only a small number of explicit same-host article redirects.
    # Never follow to a login/challenge host or leak the URL in an error.
    seen=set()
    for _ in range(4):
        if link in seen:
            raise ValueError("微信原文重定向循环")
        seen.add(link)
        r=s.get(link,timeout=(8,23),allow_redirects=False)
        if r.status_code in (301,302,303,307,308):
            location=r.headers.get("Location")
            target=urljoin(link,location) if location else ""
            if not is_wechat_url(target):
                # Diagnostics deliberately exclude raw locations and queries.
                try:
                    parsed=urlsplit(target)
                    scheme=parsed.scheme if parsed.scheme in ("http","https") else "other"
                    host="wechat" if parsed.hostname=="mp.weixin.qq.com" else "other"
                    path="article" if parsed.path=="/s" or parsed.path.startswith("/s/") else "other"
                    form=("missing" if not location else "protocol-relative" if location.startswith("//")
                          else "absolute" if urlsplit(location).scheme else "relative")
                except ValueError:
                    scheme=host=path=form="invalid"
                raise ValueError(f"微信原文重定向目标不受信任或缺失 status={r.status_code} form={form} scheme={scheme} host={host} path={path}")
            link=target
            continue
        if r.status_code!=200:
            raise ValueError(f"微信原文 HTTP 状态 {r.status_code}")
        if len(r.content)>6_000_000:
            raise ValueError("微信原文响应超过大小上限")
        break
    else:
        raise ValueError("微信原文重定向次数超过上限")
    u=urlsplit(link)
    soup=BeautifulSoup(r.content,"lxml")
    title_el=soup.select_one("#activity-name")
    publisher=soup.select_one("#js_name")
    body_el=soup.select_one("#js_content")
    if not title_el or not publisher or not body_el:
        raise ValueError("微信文章页不包含完整原文结构")
    title=title_el.get_text(" ",strip=True)
    name=publisher.get_text(" ",strip=True)
    if name!=ACCOUNT or normalize(title)!=normalize(expected):
        raise ValueError("公众号身份或文章标题不符")
    biz=re.search(r"\b(?:var|let|const)\s+biz\s*=\s*['\"]([^'\"]+)['\"]",r.text)
    if not biz or biz.group(1)!=BIZ:
        raise ValueError("公众号唯一标识不匹配")
    body=body_el.get_text(" ",strip=True)
    if len(body)<300:
        raise ValueError("文章正文不足，不能当成有效历史文章")
    mid=re.search(r"\b(?:var|let|const)\s+mid\s*=\s*['\"](\d{5,20})['\"]",r.text)
    idx=re.search(r"\b(?:var|let|const)\s+idx\s*=\s*['\"](\d{1,4})['\"]",r.text)
    if not mid or not idx:
        raise ValueError("文章原始唯一 ID 缺失")
    datepart=date_in_title(title).isoformat()
    source_kind="stable_wechat" if ("/s/" in link or (parse_qs(u.query).get("sn") or [""])[0]) else "temporary_tencent"
    return {
        "reviewId":f"{ACCOUNT_ID}_mid{mid.group(1)}_idx{idx.group(1)}",
        "name":ACCOUNT,"title":title,"date":datepart,
        "url":link,"summary":body[:240],
        "historySource":"sogou_verified",
        "linkKind":source_kind,
        "lookupUrl":stable_lookup(title),
        "verifiedChars":len(body)
    }

def progress_load():
    if not PROGRESS.exists():
        return {}
    obj=json.loads(PROGRESS.read_text(encoding="utf-8"))
    return obj if isinstance(obj,dict) else {}

def progress_save(obj):
    PROGRESS.parent.mkdir(parents=True,exist_ok=True)
    temp=PROGRESS.with_suffix(".tmp")
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(PROGRESS)

def fetch_backfill(existing,days=30,max_attempts=5,pause=1.3,progress_path=None,now=None,
                   s=None, queue_path=None):
    """Returns (new_verified_entries, stats). At most max_attempts network lookups.

    Full 30-day backfill accumulates over scheduled runs. Failed matches are
    retried after 12 hours, not every 30 minutes.
    """
    own_session=s is None
    s=s or session()
    now=now or datetime.now(timezone.utc)
    current_dates={(x.get("date"),normalize(x.get("title",""))) for x in existing}
    state=progress_load() if progress_path is None else (
        json.loads(Path(progress_path).read_text(encoding="utf-8"))
        if Path(progress_path).exists() else {})
    attempts=state.get("attempts") or {}
    import pending_queue
    queue_path=Path(queue_path) if queue_path else ROOT/"pending-state.json"
    queue=pending_queue.load(queue_path)
    try:
        discovered=index_titles(s,days=days)
        degraded=False
    except requests.RequestException:
        discovered=[]
        degraded=True
    pending_queue.merge(queue,discovered,existing,normalize)
    pending_queue.save(queue_path,queue)
    cutoff=min((x["date"] for x in existing),default=None) if len(existing)>=90 else None
    candidates=pending_queue.select(queue,now,max_attempts,newer_than=cutoff)
    outside_window=sum(1 for item in queue["pending"].values()
                       if cutoff is not None and item["date"]<=cutoff)
    new=[]
    stats={"index":len(discovered),"attempted":0,"added":0,"unresolved":0,
           "temporary_links":0,"skipped_retry":0,"external_source_unavailable":degraded,
           "pending":len(queue["pending"]),"outside_window":outside_window}
    for candidate in candidates:
        if stats["attempted"]>=max_attempts:break
        key=candidate["date"]+"|"+normalize(candidate["title"])
        if (candidate["date"],normalize(candidate["title"])) in current_dates:
            continue
        stats["attempted"]+=1
        try:
            direct=candidate.get("wechat_url")
            if direct:
                try:
                    verified=validate_wechat_page(s,direct,candidate["title"])
                except (requests.RequestException,ValueError):
                    # An unusable index link is not trusted; try ordinary
                    # public search without bypassing any access controls.
                    direct=None
            if not direct:
                href=search_candidates(s,candidate["title"])
                if not href:
                    raise ValueError("搜狗未找到该公众号的匹配文章")
                link=sogou_to_tencent(s,href)
                verified=validate_wechat_page(s,link,candidate["title"])
            new.append(verified)
            current_dates.add((verified["date"],normalize(verified["title"])))
            stats["added"]+=1
            if verified["linkKind"]=="temporary_tencent":
                stats["temporary_links"]+=1
            attempts.pop(key,None)
            pending_queue.attempted(queue,candidate,now,normalize,"verified; awaiting publication")
        except (requests.RequestException,ValueError) as e:
            stats["unresolved"]+=1
            attempts[key]={"at":now.isoformat(),"reason":str(e)[:110]}
            pending_queue.attempted(queue,candidate,now,normalize,str(e))
        pending_queue.save(queue_path,queue)
        if progress_path is None:
            state["attempts"]=attempts
            state["last_scan"]=now.isoformat()
            progress_save(state)
        else:
            path=Path(progress_path)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps({"attempts":attempts,"last_scan":now.isoformat()},ensure_ascii=False),encoding="utf-8")
        time.sleep(max(0,pause))
    return new,stats
