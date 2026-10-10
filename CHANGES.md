# Changes

## 0.2.0

### Breaking changes

- Generated RSS feeds and the reader now include only entries published within
  the last 24 hours. Older items no longer appear in those views.
- Database entries are automatically deleted seven days after their
  latest fetch. Refreshing an entry updates its fetch time and restarts that
  retention period. This affects stored FeedVanta data only; it does not remove
  entries from the original feeds.

### Added

- Feed detail pages show filtered entries and explain the reason using the
  currently active filter rules.
- Feed refresh discovers a website favicon when the source feed provides no
  icon.
- Added `uv` dependency locking and setup guidance.
- Refresh intervals can be changed from the feed configuration page.
