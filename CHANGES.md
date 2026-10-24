# Changes

## 0.6.0

### Added

- Rules can target original RSS/Atom entry fields, including custom elements such
  as `authors`, with namespace-aware selection and per-occurrence matching.
  Existing normalized fields and the meaning of `all` remain unchanged.
- Added an administrator-only source field catalog API and unavailable-field
  notices; rules remain editable when their original fields disappear.
- Administrators can inspect original source XML with readable indentation and
  preserved field names/namespaces. Inspection fetches fresh data without changing
  stored articles or refresh state; embedded HTML is displayed as text.
- Filter rules can be edited in the feed settings and through the API; saving
  immediately reapplies filters to the affected output without changing siblings.
- Feed settings group filter rules and filtered entries into keyboard-accessible
  tabs, with rule and filtered entry counts shown in the tab labels.

### Changed

- Database upgrades preserve existing articles and rules; original field values
  become available only when those articles are fetched again. Retention also
  removes original field values, not the rules referencing them.
- Configuration exports use schema 3; schemas 1 and 2 still import through strict
  conversion. Older application versions cannot import schema 3 exports.
- Source refreshes reject DTD/entity definitions and enforce response, XML depth,
  entry-count, field-count, and text-size limits without partial article updates.
  Malformed XML retains tolerant ingestion but exposes no original field values
  for that refresh and shows an extraction warning.

## 0.5.0

### Added

- Sources now support multiple named output feeds with independent rules and
  individual RSS/reader URLs, sharing downloaded articles and refresh state.
- The overview groups outputs under their source, with shared settings, explicit
  source deletion confirmation, output creation, and output renaming.
- Added source management APIs and automatic transactional database migration;
  existing output IDs, public URLs, rules, article data, and users are preserved.
- Legacy upgrades preserve compatible download/digest and other extension tables,
  including article references, and initialize missing authentication/configuration
  tables from older installations. Incompatible layouts still abort safely.
- Configuration imports accept legacy exports and convert them to the source/output
  format. New exports use document-local source references.

### Changed

- Output names now determine RSS and reader titles. During database upgrade,
  each existing effective title becomes its output name; later source title
  changes no longer overwrite it.
- Deleting an output keeps its source, shared articles, and sibling outputs.
  Confirmed source deletion removes all of that source's outputs and articles.
- Refresh intervals and manual refreshes affect all outputs of the same source.
- Configuration replacement removes all existing sources, outputs, rules, and
  cached articles and may change subscription URLs. Authentication data is kept.
  Startup database upgrades do not themselves prune articles; normal seven-day
  retention and the 24-hour publication window remain unchanged.

## 0.4.0

### Changed

- FeedVanta releases now publish Python distributions on GitHub Releases for
  installation by PyHost deployments; the standalone container release was
  removed.

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
