"""Persistent, bounded fair retry queue for discovered article titles."""
import datetime as dt
import json
import time
from urllib.parse import urlsplit
from pathlib import Path


def key(item, normalize):
    return item['date'] + '|' + normalize(item['title'])


def load(path):
    path = Path(path)
    if not path.exists():
        return {'pending': {}}
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or not isinstance(obj.get('pending'), dict):
        raise ValueError('Invalid pending queue; refusing to overwrite')
    return obj


def save(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for attempt in range(4):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))


def merge(state, discovered, existing, normalize):
    pending = state['pending']
    published = {key(x, normalize) for x in existing}
    for k in published:
        pending.pop(k, None)
    for item in discovered:
        k = key(item, normalize)
        if k not in published:
            if k not in pending:
                pending[k] = {'date': item['date'], 'title': item['title'], 'attempts': 0}
            url = item.get('wechat_url', '')
            if url:
                from sleepnews_history import canonical_article_url
                try:
                    pending[k]['wechat_url'] = canonical_article_url(url)
                except ValueError:
                    pass


def select(state, now, limit, cooldown=dt.timedelta(hours=12), newer_than=None):
    eligible = []
    for k, item in state['pending'].items():
        if newer_than is not None and item['date'] <= newer_than:
            continue
        last = item.get('last_attempt')
        if last:
            try:
                when = dt.datetime.fromisoformat(last)
                if when.tzinfo is None:
                    raise ValueError('naive timestamp')
                if now - when < cooldown:
                    continue
            except (TypeError, ValueError):
                pass
        eligible.append((item.get('last_attempt') or '', item['date'], k))
    # Reserve up to two slots for the newest eligible articles and at least
    # one for oldest-attempted backlog when three slots are available.
    if not eligible or limit <= 0:
        return []
    recent_count = min(2, limit if limit < 3 else limit - 1)
    recent = sorted(eligible, key=lambda row: (row[1], row[2]), reverse=True)[:recent_count]
    rest = sorted(row for row in eligible if row not in recent)
    return [state['pending'][row[2]] for row in recent + rest[:limit-len(recent)]]


def attempted(state, item, now, normalize, outcome):
    entry = state['pending'][key(item, normalize)]
    entry['last_attempt'] = now.isoformat()
    entry['attempts'] = int(entry.get('attempts', 0)) + 1
    entry['last_outcome'] = str(outcome)[:80]
