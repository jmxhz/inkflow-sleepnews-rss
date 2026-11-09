## Stable Pages fulltext subscription (2026-11-09)

New dedicated source for WhereMyLife: https://jmxhz.github.io/inkflow-sleepnews-rss/feed-stable.xml

This is distinct from the legacy https://jmxhz.github.io/inkflow-sleepnews-rss/feed.xml .
The legacy feed must not change its already published item URLs because the
WhereMyLife backend has cached multiple URL variants of some older articles
and duplicates them. The old source also retains some expired Sogou-signed
WeChat links.

The new source has immutable GitHub Pages article links under stable/*.html,
complete article content:encoded + description, and locally mirrored article
images in assets/*.jpg. All future articles use the same permanent URL scheme.
When migrating to WhereMyLife, subscribe to the new feed and unsubscribe from
both legacy same-name entries to prevent duplicate Kindle publications.
Only the WhereMyLife backend can clear already cached legacy duplicates.

.github/workflows/update-rss.yml runs discovery/hydration AND builds both public
feeds and permanent pages/images before publishing atomically. An intermittent
network timeout from the third-party history index does not delete previously
verified fulltext; discovery is retried on the next schedule. Scheduled GitHub
Actions execution may be delayed. Actual WhereMyLife/Kindle re-ingestion must
still be tested as a separate third-party service integration.

## Current publication mode (2026-11-08)

The official GitHub Pages feed.xml now contains complete verified article HTML in both RSS content:encoded and description, including publisher image links. Previously published item IDs, original URLs and publication dates remain unchanged. GitHub Actions must hydrate new articles before commit and rejects incomplete articles rather than silently shipping summaries. Publication was authorized by the user. WhereMyLife may still separately cache old entries; real Kindle delivery requires a new end-to-end check.

# inkflow-sleepnews-rss
Read-only daily SleepNews RSS excerpts and original publisher links
