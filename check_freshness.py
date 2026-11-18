"""Fail visibly when publication is stale; run after state is persisted."""
import datetime as dt
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime


def check(feed, queue, now, max_age_days=3):
    root = ET.parse(feed).getroot()
    dates = [parsedate_to_datetime(x.text) for x in root.findall('./channel/item/pubDate') if x.text]
    if not dates:
        return ['No published article dates']
    latest = max(dates)
    warnings = []
    if now - latest > dt.timedelta(days=max_age_days):
        warnings.append('Newest published article is older than %d days' % max_age_days)
    if Path(queue).exists():
        state = json.loads(Path(queue).read_text(encoding='utf-8'))
        pending = state.get('pending', {})
        if pending:
            warnings.append('%d discovered articles remain pending' % len(pending))
    return warnings


if __name__ == '__main__':
    warnings = check(sys.argv[1] if len(sys.argv) > 1 else 'feed.xml', 'pending-state.json', dt.datetime.now(dt.timezone.utc))
    for warning in warnings:
        print('::warning::' + warning)
    if any('older than' in x or 'No published' in x for x in warnings):
        sys.exit(1)
