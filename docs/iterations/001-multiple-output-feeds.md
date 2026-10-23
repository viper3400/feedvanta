# Iteration 001: Multiple filtered outputs per source

- Status: Implemented; release not assigned
- Planned: 2026-10-22
- Scope: One iteration covering persistence, migrations, configuration, services,
  API, UI, tests, and documentation
- Release: To be assigned when implemented

## Goal

Allow one original RSS/Atom source to produce multiple named output feeds with
independent filters. Download and store source entries once, while giving each
output its own reading view and RSS subscription URL.

Example:

```text
Technology News (original source; one refresh interval)
├── Python News (Python rules; own reader and RSS URL)
├── Security News (security rules; own reader and RSS URL)
└── All News (no rules; own reader and RSS URL)
```

This document records the complete iteration rather than separate releases or
iterations for migration infrastructure, backend work, and UI work. Implementation
steps may be split into focused commits, but the feature ships together.

## Current behavior

- A `feeds` row combines source URL, refresh state, name, and publication identity.
- Source URLs are unique. Both API and form creation reject duplicates with 409.
- Filter rules and entries belong to a feed. Entry visibility is currently stored
  in a single `entries.hidden` field.
- Refreshing a feed downloads its source and evaluates its rules.
- Public outputs use `/feed/{feed_id}.xml` and `/reader/{feed_id}`; deployments may
  prepend the host/plugin path.
- RSS and reader views include only visible entries published within 24 hours.
  Entries without a publication timestamp are excluded.
- Stored entries expire seven days after their latest fetch.
- Configuration schema version is 1. Imports replace the configuration and its
  stored entries; exports omit runtime data and authentication data.
- Database initialization currently uses table creation and column checks, without
  an explicit database schema version.

Relevant code: `app/db.py`, `app/services.py`, `app/configuration.py`,
`app/main.py`, `app/version.py`, and `app/templates/`.

## Agreed requirements and decisions

1. Model original sources separately from generated output feeds.
2. Keep each original source URL unique; do not permit duplicate source rows as a
   shortcut for multiple outputs.
3. Give every output its own name and completely independent filter rules. There
   is no parent-rule inheritance or filter chaining.
4. Share source downloads, metadata, refresh settings, and stored entries.
5. Preserve existing output IDs and public RSS/reader URLs during database upgrade.
6. Migrate existing databases automatically on startup, before serving requests or
   starting scheduled refresh work.
7. Accept old configuration exports through explicit import migration. Export only
   the new configuration schema after this iteration.
8. Track database structure, configuration format, and configuration edits with
   separate version numbers.

### Alternative considered

Allowing the same URL to be added repeatedly under different names would require
less restructuring, but would duplicate requests, entries, refresh state, and
source settings. The source/output model avoids those inconsistencies and makes
the relationship clear in the UI.

## Proposed persistence design

These are implementation targets, not a description of an existing schema.

| Entity | Ownership and responsibilities |
| --- | --- |
| `sources` | Unique URL, enabled state, refresh interval, last fetch/error, discovered title/icon, creation time |
| `feeds` | Stable output ID, source foreign key, output name, creation time |
| `filter_rules` | Rules belonging to one output feed; existing rule semantics |
| `entries` | Source foreign key and normalized article data; unique `(source_id, guid)` |
| Output visibility | Per-output filtering result for each shared entry |

Retain `feeds` as the output entity to reduce disruption to existing IDs, rule
ownership, and public routes. Source IDs and output IDs are distinct identities;
never assume they match after migration.

Use a relation such as `feed_entry_visibility(feed_id, entry_id, hidden)` with a
unique output/entry pair, foreign keys, and suitable lookup indexes. A shared
`entries.hidden` value must not determine visibility for every output. Missing
visibility rows must not accidentally expose articles: output creation, source
refresh, and rule changes must populate or update results consistently.

### Lifecycle and behavior

- Creating a source normally creates its first named output in the same operation.
- Adding another output uses existing entries immediately, without a new fetch.
- Outputs without active rules show all otherwise eligible source entries.
- Adding or deleting rules recalculates only the affected output.
- A source refresh upserts entries once and updates visibility for all its outputs.
- A manual refresh from an output refreshes its shared source; the UI must explain
  that all sibling outputs receive refreshed content.
- Refresh intervals and fetch errors appear as source settings/state, not editable
  copies on outputs. Source disabling controls scheduled fetching; it does not
  introduce a new rule for hiding already stored public content.
- Deleting an output deletes its rules and visibility results, not its source,
  sibling outputs, or shared entries. A source may remain with no outputs.
- Deleting a source with outputs requires an explicit destructive confirmation;
  it removes those outputs, rules, entries, and visibility results.
- Retention remains source-entry based: seven days since the latest fetch.
  Deleting an expired entry also removes its visibility results.
- Source icons remain shared. Named outputs use their configured names in the UI,
  reader, and RSS title so siblings are distinguishable.
- During upgrade, preserve each existing effective display title
  (`source_title` when present, otherwise `name`) as the output name. This avoids
  an immediate subscription-title change; preserve the original source title
  separately as metadata. Document this name conversion in the release notes.

## Database migration and version tracking

Use SQLite `PRAGMA user_version` for the database schema version, independently
of the exported configuration `schema_version`.

Suggested layout:

```text
app/migrations/
  __init__.py
  v001_split_sources_and_outputs.py
```

- Treat a recognized existing, unversioned database as legacy version 0.
- Distinguish an empty database from the known legacy schema. Reject unexpected
  layouts with a clear diagnostic rather than guessing and destroying data.
- The first numbered migration produces database schema version 1. This numbering
  is independent of configuration schema version 2.
- Apply schema changes, data conversion, integrity checks, and the version update
  transactionally. SQLite DDL/transaction handling must be explicit; avoid implicit
  commits from initialization helpers breaking migration atomicity.
- Serialize startup migration work with an appropriate write transaction and
  re-check the version after obtaining the lock.
- Reject a database from a newer unsupported schema before running writes.
- Fresh installations must reach the same schema and version as upgraded ones.
- Keep numbered migrations in the repository so older installations can upgrade.

### Legacy data conversion

1. Create one source for every existing feed, carrying over URL, refresh settings,
   enabled state, fetch/error state, discovered metadata, and creation time.
2. Keep each feed as its first output with its existing ID and rules.
3. Reassign existing entries to the corresponding source, retaining entry IDs,
   GUIDs, content, publication timestamps, and fetch timestamps.
4. Transfer existing `hidden` values to that output's visibility relation.
5. Preserve users, permissions, and the configuration revision. Structural upgrade
   is not a user configuration edit.
6. Check foreign keys, row mappings, uniqueness, and version before committing.

No article pruning belongs inside the migration. Normal retention resumes after
successful startup. On migration failure, roll back, stop startup with an
actionable error, and leave the prior database intact.

Operators should back up the database before upgrading, using a consistent SQLite
backup or stopping the application before copying it. Downgrade migration is out
of scope; rolling back to the old application requires restoring the backup.

## Configuration schema version 2

Increase `CONFIG_SCHEMA_VERSION` from 1 to 2. Keep `app_version`,
`schema_version`, and `config_version` distinct from the database version.

A proposed self-contained export uses document-local source keys, not database
IDs. Public output IDs are not part of this example or a promise of import-time
URL preservation:

```json
{
  "app_version": "<release version>",
  "schema_version": 2,
  "config_version": 12,
  "exported_at": "2026-10-22T10:00:00+00:00",
  "sources": [
    {
      "key": "source-1",
      "source_url": "https://example.com/feed.xml",
      "enabled": true,
      "refresh_interval": 30
    }
  ],
  "feeds": [
    {
      "name": "Python News",
      "source": "source-1",
      "rules": [
        {
          "field": "title",
          "operator": "contains",
          "value": "Python",
          "action": "include",
          "enabled": true
        }
      ]
    },
    {"name": "All News", "source": "source-1", "rules": []}
  ]
}
```

### Import migration

- Inspect the schema version before parsing with the current document model.
- Parse and validate version 1 using its own strict model, then convert each legacy
  feed to one source and one output. Preserve its name, URL, enabled state,
  interval, rules, and configuration revision.
- Validate the migrated version 2 document before changing persistent data.
- Validate unique normalized source URLs, unique source keys, valid references,
  rule fields, and existing value/interval limits. Multiple outputs referencing
  the same source are valid; duplicate source definitions are not.
- Keep explicit, testable migration functions, for example
  `app/config_migrations/v1_to_v2.py`; do not silently relax version validation.
- Reject unsupported versions, invalid data, or invalid references without
  modifying the database or revision.
- Replace the configuration atomically after validation, retaining current import
  semantics: downloaded entries are removed, users and permissions are untouched,
  and `config_version` is taken from the imported document.
- Explain in the import UI that replacement discards cached articles and can
  change subscription URLs. Startup database migration preserves URLs; replacement
  import does not newly guarantee that property.
- Subsequent exports always use version 2, including after a version 1 import.
- No startup configuration-file migration is required: configuration is currently
  persisted in SQLite, not automatically loaded from a JSON file.

Configuration edits to sources, outputs, and rules increment the configuration
revision transactionally. Refreshes, retention, and structural migrations do not.

## API and interface

### API direction

- Introduce source-oriented list/create/update/delete/refresh operations.
- Keep output feed listing, rule management, and public feed/reader identities.
- Allow output creation by referencing an existing source.
- Preserve the existing URL-based create request as a convenience for creating a
  new source plus its first output. Duplicate URLs still return 409 and direct the
  caller to create another output from the existing source.
- Existing output-level manual refresh and interval endpoints can delegate to the
  linked source; document that interval changes affect all sibling outputs.
- Output deletion no longer implies deleting the underlying source and entries;
  document this intentional administrative API behavior change.
- Finalize request/response models and concrete source routes during implementation
  and record them in the outcome section below.

### UI direction

- Group the overview by source, showing shared refresh/error state and its outputs.
- Provide **Add filtered feed** on each source, requesting an output name.
- Show **Lesen**, **RSS**, and output configuration actions for each output.
- Separate source settings from output-specific rules; use clear shared-setting
  descriptions and explicit deletion confirmations.
- Keep rule explanations and visible/filtered counts specific to each output.
- Preserve public access to RSS/reader views and administrative protection for all
  configuration, source management, and output management operations.
- Preserve plugin mounting and reverse-proxy prefix handling in all generated URLs.

## Compatibility and non-goals

- Existing public RSS/reader paths and IDs survive database upgrade.
- Keep the 24-hour publication window, exclusion of missing timestamps, RSS GUIDs,
  enclosure behavior, and existing reader/RSS result limits.
- Keep include/exclude semantics and case-insensitive matching unchanged.
- Do not add nested outputs, inherited rules, multiple sources per output,
  cross-source deduplication, new filter operators, or a new authentication model.
- URL uniqueness follows existing URL validation/normalization. Detecting equivalent
  sources through redirects or aggressive URL canonicalization is out of scope.
- Do not introduce a dependency on PyHost or change its structural plugin contract.
- No new dependency is expected. If necessary, manage it with `uv` and regenerate
  `uv.lock` rather than editing the lockfile manually.

## Implementation checklist

All items belong to this single iteration. Ordering reflects dependencies, not
separate delivery milestones.

- [x] Finalize schema, visibility relation, and API models against these decisions.
- [x] Add the versioned database migration runner and legacy conversion.
- [x] Add configuration schema 2, strict schema 1 conversion, and atomic import.
- [x] Refactor fetching, retention, and metadata ownership to sources.
- [x] Implement independent output visibility and immediate rule reapplication.
- [x] Adapt public RSS/reader queries, counts, names, and rule explanations.
- [x] Add source/output API and form operations with authorization and validation.
- [x] Update overview and configuration pages, confirmations, and shared settings.
- [x] Add migration, service, API, UI, and compatibility regression tests.
- [x] Update `README.md` with the new model, export format, API, and upgrade steps.
- [x] Update `CHANGES.md` with user-facing changes, migration/name conversion,
  deletion behavior, and import data-retention effects; do not list version bumps
  as standalone change-log entries.
- [x] Run checks, record results, and complete the historical outcome below.

## Acceptance and verification

### Migration tests

- Fresh initialization and legacy upgrade produce equivalent schemas and versions.
- Upgrade preserves output IDs/URLs, rules, article data/timestamps, visibility,
  source metadata/state, users, permissions, and configuration revision.
- Cover legacy databases with and without the optional metadata columns currently
  added by initialization; cover empty feeds and disabled sources.
- Repeated startup is idempotent; migration is not run again after success.
- Forced failure leaves no partial tables, data conversion, or version update.
- Unknown database versions/layouts fail safely; foreign key checks pass.

### Configuration tests

- Version 1 import converts correctly and re-exports as version 2.
- Version 2 round-trip supports multiple outputs sharing one source and independent
  rule sets, including an output without rules.
- Reject duplicate URLs/keys, missing references, invalid rules/intervals, malformed
  documents, and unsupported versions without changing existing data.
- Successful replacement preserves authentication data and the imported revision,
  removes cached entries, and creates the intended source/output relationships.

### Behavior and compatibility tests

- One source with conflicting output rules produces different reader/RSS results
  without duplicating stored articles or fetching once per output.
- New outputs immediately filter existing entries. Rule changes affect no siblings.
- Refresh updates all output visibility; failed refresh preserves stored articles.
- Scheduler and interval changes act on sources, including disabled and zero-output
  sources. Output deletion leaves source/sibling data; confirmed source deletion
  cascades only to that source's data.
- Retention removes expired shared entries and their visibility results while
  retaining entries fetched within seven days, regardless of publication age.
- Test publication timestamps just inside, exactly at, and outside 24 hours using a
  fixed clock, including missing timestamps and equivalent timezone offsets.
- Preserve public route identities, anonymous access, prefix-aware URLs, RSS GUIDs,
  enclosures, and configured output titles after upgrade.
- Require administrator access for new mutation endpoints/forms. Duplicate source
  creation remains 409 while multiple outputs from one source succeed.

Run `uv run pytest`. If templates introduce new Tailwind classes, regenerate the
stylesheet with `npm ci` and `npm run build:css` and review the resulting changes.
Record any checks that cannot run rather than implying success.

## Delivery and historical outcome

Update this section as implementation proceeds and upon completion. Preserve the
original intent above; explain deviations here rather than silently rewriting
the plan to match the implementation.

- Implementation status: Implemented in the working tree on 2026-10-22.
- Delivered release/date: No release assigned or published.
- Related commit: `feat: support multiple filtered outputs per source` (2026-10-23).
  No pull request created by this task.
- Final database/configuration versions: Database 1 / configuration 2. The
  application release version is unchanged.
- Persistence: `sources`, output `feeds`, source-owned `entries`, and
  `feed_entry_visibility(feed_id, entry_id, hidden)`. Output creation, rule changes,
  and visibility calculation share a write transaction; absent results are never
  treated as public visibility. Deleted legacy output ID high-water marks survive
  migration to prevent reuse of old subscription identities.
- Final API routes:
  - `GET /api/sources`: sources with their outputs.
  - `POST /api/sources`: `name`, `source_url`, optional `refresh_interval`; creates
    the source and first output and returns source `id` and output `feed_id`.
  - `PATCH /api/sources/{id}`: shared `enabled` and/or `refresh_interval`.
  - `POST /api/sources/{id}/refresh`: refresh all outputs through their source.
  - `DELETE /api/sources/{id}`: requires `?confirm=true` when outputs exist.
  - `POST /api/feeds`: either `name` plus existing `source_id`, or the legacy
    URL-based request for a new source plus first output. Source references must
    not include a refresh interval; update the shared source separately.
  - `PATCH /api/feeds/{id}`: change `name`.
  - Existing rule, output-delete, refresh, interval, RSS, and reader routes remain;
    output deletion keeps shared source data and interval/refresh operations
    delegate to the source.
- Forms: source output-creation, settings, refresh, and deletion actions live under
  `/sources/{source_id}/…`; output name changes use `/feeds/{feed_id}/name`.
- Configuration: implemented the proposed schema 2 document-local source keys and
  separate schema 1 validation/conversion. Configuration replacement is atomic,
  discards articles, and retains users and permissions.
- Decisions refined during implementation:
  - Source updates expose scheduling settings, not URL reassignment; changing a
    source URL in place is not part of this iteration.
  - Publication comparisons use a deterministic SQLite function backed by Python
    ISO timestamp parsing, preserving timezone and microsecond boundary behavior.
  - Reject unknown core layouts, conflicting new object names, custom indexes on
    rebuilt core tables, triggers, and views. Preserve compatible extension tables
    and their indexes without rewriting them. Earlier installations can lack the
    authentication/configuration tables; initialize those missing tables normally.
    This refines the initial exact-table guard, which rejected real installations
    containing historical download and topic-digest tables.
  - Disable foreign-key enforcement only on the migration connection before its
    transaction, preventing table rebuilds from cascading into extension data.
    Validate all foreign keys before commit and restore enforcement afterward;
    incompatible references cause rollback rather than partial migration.
- Migration/backup instructions verified: tests cover fresh/upgraded schema
  equivalence, optional legacy metadata columns, all preserved data categories,
  idempotence, concurrent startup, transactional rollback, unsupported layouts,
  public URL continuity, and SQLite backup/restore of the pre-upgrade database.
  Follow-up regressions cover core-only databases, compatible extension data/index
  preservation, cascading references after migration, and rollback on incompatible
  extension foreign keys. A backup copy of local `data/feed-lens.db` migrated
  successfully with all 95 entries and both outputs retained. A backup copy of
  `data/feedvanta.db`, already at schema 1 when copied, passed initialization and
  integrity checks with historical extension tables retained.
- Test and build results: `uv run pytest` passed (74 tests); `npm ci` and
  `npm run build:css` succeeded; stylesheet regenerated. `git diff --check` passed.
  npm reported an unapproved optional watcher install script and the CSS build
  emitted a Node deprecation warning; neither prevented the build.
- Checks not performed: manual browser walkthrough, migration against a live
  production database, or an external RSS source. Automated tests use isolated SQLite
  databases, real feed parsing with mocked HTTP, and rendered HTML/API requests.
- Known limitations: database downgrade requires restoring a backup; replacement
  config imports do not guarantee subscription URL preservation; outputs with
  no rules are intentionally unfiltered within the existing publication window.
