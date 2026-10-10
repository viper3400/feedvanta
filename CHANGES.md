# Changes

## 0.3.0

### Changed

- Extracted the ASGI plugin host into the independently installable sibling
  project `pyhost`, removed FeedVanta's runtime dependency on the host, and
  raised the minimum Python version to 3.13.
- Replaced standalone container releases with Python package assets on GitHub
  Releases for installation by a PyHost deployment.
- Refreshed the web interface with a consistent modern visual style.
- Applied the Tailwind design to the central tools landing page.

### Added

- Added a minimal example plugin and documentation for the PyHost plugin contract.
- Feed refresh intervals can be edited from each feed's configuration page.
- Replaced inline page styles with a locally served, Tailwind-generated stylesheet.

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
