# Iteration 002: Rules for additional source fields

- Status: Implemented in the working tree; assigned to 0.6.0
- Planned: 2026-10-24
- Scope: One iteration covering source parsing, persistence, migrations,
  filtering, configuration, API, UI, tests, and documentation
- Release: 0.6.0

## Goal

Allow rules to target fields present in original source entries that FeedVanta
does not currently expose, without mapping those fields onto standard fields.

For example, an entry containing:

```xml
<authors>hans dampf, Gaby Mueller, Redaktion</authors>
```

should offer an additional `authors` field. A rule selecting that field with
`contains`, `Redaktion`, and `exclude` should hide the entry for that output.
The existing normalized `author` field must retain its current meaning.

## Current behavior

- `normalize_entry` stores normalized title, content, author, category, URL,
  identity, and publication/fetch timestamps, not arbitrary original fields.
- The author value comes from feedparser's singular `author` key. A custom
  `<authors>` string is not automatically included in that value.
- Rule fields are restricted to `title`, `content`, `author`, `category`, and
  `all` in SQLite, API/form validation, and configuration models.
- `all` searches the four existing normalized text fields.
- Entries belong to sources; rule definitions and visibility belong to outputs.
- Source inspection shows freshly fetched original XML but does not persist it
  or change entries. It is not the data source for rule evaluation.
- Database schema version is 1; exported configuration schema version is 2.

Relevant code: `app/services.py`, `app/db.py`, `app/migrations/`,
`app/configuration.py`, `app/config_migrations/`, `app/main.py`,
`app/templates/feed_detail.html`, and `app/source_inspection.py`.

## Requirements and scope

1. Keep existing standard field identifiers, matching behavior, and rules intact.
2. Discover additional fields from successfully fetched RSS items and Atom entries
   for each source. Do not infer them from another source or output.
3. Offer original entry fields in a separate selector group. Original XML fields
   may overlap with normalized fields: label them clearly rather than silently
   aliasing or replacing a normalized field.
4. Persist extracted field values with each shared source entry so new outputs
   and rule edits work without an additional network request.
5. Keep source discovery and stored values shared while each output retains
   independent rules and visibility.
6. Support existing contains, equals, and regex operators with unchanged
   case-insensitive behavior. Do not introduce new operators in this iteration.
7. Keep `all` limited to its existing normalized fields.
8. Preserve unavailable field selections and imported rules; never silently
   retarget them or delete them when a source changes.
9. Preserve public RSS/reader URLs, output names, publication limits, retention,
   and the independent PyHost structural contract.

## Field identity and extraction

Use an explicit identifier namespace for original fields, separate from the
five legacy identifiers. A proposed canonical representation is `xml:` followed
by a JSON array containing namespace URI and local name:

```text
author
xml:["","authors"]
xml:["http://example.com/editorial","authors"]
```

The `xml:` representation must have one shared parser/serializer used by storage,
API, forms, and configuration validation. Validate its exact structure and XML
local name; reject arbitrary field strings. Canonicalize insignificant JSON
formatting so equivalent identifiers cannot produce duplicate fields.

- XML field identity is case-sensitive and based on namespace URI plus local
  name, never on the source's choice of namespace prefix.
- Namespace prefixes may be shown as hints, but labels must distinguish identical
  local names belonging to different namespaces. Show the URI where needed.
- Discover direct child elements of RSS `<item>` and Atom `<entry>` only.
  Feed/channel metadata, XML attributes, and arbitrary nested paths are excluded.
- Extract each element's descendant text in document order, including CDATA.
  Decode XML entities normally; do not execute HTML or strip HTML markup stored
  inside CDATA or escaped text. Do not add separators inside mixed-content text.
- Repeated elements produce an ordered list of independent string values.
  Whitespace-only values count as absent; document the trimming policy and test
  meaningful internal whitespace and mixed content.
- Elements containing only attributes and no text have no searchable value.
- Use raw XML identities, not feedparser's flattened extension-key names, which
  may lose namespace distinctions. Continue using feedparser for existing
  normalized fields and supported-feed behavior.

Finalize and document the extraction/correlation strategy before implementing
the parser. Extracted values must attach to the correct normalized entry even
with missing or duplicate GUIDs, skipped entries, and reordered input. Do not
assume two independently parsed lists can simply be zipped. Keep downloads
shared: extraction must use the same downloaded response as normal refresh.

## Matching and availability

- A custom-field rule matches if any nonempty occurrence matches the operator.
  `equals` compares one occurrence, not a concatenation of repeated elements.
- A comma-separated `<authors>` string is one value; contains `Redaktion` matches
  it without interpreting or splitting author names.
- A missing or empty custom field does not match, including regex patterns that
  would otherwise match the empty string. Keep legacy field semantics unchanged.
- Preserve existing include/exclude combination semantics. In particular, an
  include rule for a missing field can hide an entry; explain this in the UI.
- Derive currently available fields from values on retained entries for that
  source. A field absent from the latest response can remain available while
  older retained entries still contain it.
- Union this catalog with fields referenced by the affected output's rules when
  rendering its editor. Label referenced fields with no retained values as
  currently unavailable, but keep them selectable and editable.
- API/configuration rules may refer to valid canonical fields not yet discovered.
  Availability is runtime information, not configuration validity.

## Persistence and migration

Target database schema version 2. Suggested additions:

- Store a JSON object of canonical custom-field IDs to lists of strings on each
  entry, defaulting to `{}` for existing rows. Do not retain entire raw feeds.
- Relax the `filter_rules.field` legacy-only CHECK constraint to admit the
  defined custom identifier family; enforce full validation at application
  boundaries. Do not accept arbitrary field names as an implementation shortcut.
- Rebuild `filter_rules` transactionally if necessary to change its constraint,
  retaining rule IDs, output ownership, values, actions, and enabled state.

Extend the migration runner to apply ordered transitions: recognized legacy
version 0 to version 1, then version 1 to version 2. Do not run the old migration
against the latest schema and incorrectly mark intermediate structure as current.
Fresh installations and all supported upgrades must reach equivalent schemas.

Preserve existing protections for extension tables, foreign keys, unknown schemas,
rollback, and concurrent initialization. Structural migrations do not increment
the configuration revision or discard articles.

Existing articles start with no custom values. Populate them only when their
entries appear in a later successful refresh; do not claim historical fields can
be reconstructed from normalized columns. A successful upsert replaces that
entry's custom values so removed fields do not linger. A failed refresh preserves
previous entries and values. Normal seven-day retention also removes field data.

## Configuration compatibility

- Export configuration schema version 3 because rule field identifiers expand
  beyond the schema 2 contract. Database and configuration versions remain
  independent.
- Accept schema 1 and 2 through explicit, strict migration into schema 3.
  Preserve legacy rule identifiers exactly; schema 1 still passes through the
  existing source/output conversion.
- Keep schema 2 validation frozen to its old field set; do not accidentally accept
  schema 3 custom identifiers in a document labelled schema 2.
- Export custom-field identifiers in rule definitions only. Do not export fetched
  values, the discovered field catalog, raw XML, or authentication data.
- Permit a schema 3 rule for a valid but undiscovered field, including immediately
  after import when cached entries have been discarded.
- Keep replacement atomic and retain its existing article-discarding semantics.
  Older application versions cannot import schema 3 exports; document this.

## API and interface

- Add an administrator-only `GET /api/sources/{source_id}/fields` returning
  canonical identifiers, display labels, namespace/local-name information, and
  availability derived from retained entries. Unknown sources return 404.
- Use the same catalog logic in feed settings. Group normalized fields separately
  from original source fields in both create and edit selectors.
- Explain that discovery occurs on refresh, older articles initially lack custom
  values, and missing fields do not match. Distinguish unavailable referenced
  fields from fields currently containing values.
- Extend rule create/update endpoints and forms using the shared identifier
  validator. Preserve existing request shapes and legacy identifiers.
- Reapply only the affected output after rule creation or editing. Refreshes
  recalculate all sibling outputs using the shared newly extracted values.
- Show meaningful custom-field labels in rule summaries and hidden-entry reasons;
  escape all source-provided labels, names, and values.
- Preserve authorization, prefix-aware URL generation, and existing public access
  to generated RSS/reader views. Do not make the source inspector public.

## Safety, compatibility, and non-goals

- Bound XML parsing, nesting, field count, and extracted text size. Reject external
  entity resolution and entity expansion; add no remote requests during parsing.
  Finalize concrete limits during implementation and document them.
- Avoid partial refresh commits if custom extraction exceeds limits or fails.
  Malformed feeds currently tolerated by feedparser need an explicit compatibility
  policy: either preserve normal ingestion with clearly unavailable custom data,
  or justify and document rejection. Do not silently turn all legacy refreshes
  into strict-XML parsing without assessing the impact.
- No XPath, attribute rules, nested-field paths, HTML rendering, author aliasing,
  automatic name splitting, new regex semantics, or cross-source field catalogs.
- Generated RSS does not publish arbitrary original XML fields; existing output
  content and enclosure behavior remain unchanged.
- Prefer existing dependencies and standard-library facilities. Manage any
  necessary new dependency with `uv` and regenerate `uv.lock`.

## Implementation checklist

- [x] Finalize identifier encoding, text extraction, entry correlation, limits,
  and malformed-feed compatibility decisions against representative fixtures.
- [x] Add database schema 2 and ordered, transactional migrations.
- [x] Extract and persist original entry fields during shared source refresh.
- [x] Add custom-field matching, availability discovery, and readable reasons.
- [x] Add strict configuration schema 3 with schema 1/2 migration and round trips.
- [x] Extend rule API/forms and add the authorized source field-catalog endpoint.
- [x] Update create/edit selectors, labels, availability notices, and help text.
- [x] Add migration, parser, filtering, API, configuration, and UI regressions.
- [x] Update `README.md` and `CHANGES.md`, including upgrade/backfill limitations
  and schema 3 export compatibility.
- [x] Run tests/build checks and record outcomes and deviations below.

## Acceptance and verification

- The example `<authors>` entry is excluded by a custom-field contains rule for
  `Redaktion`; normalized `author` rules continue behaving exactly as before.
- Standard RSS author, Dublin Core creator, and Atom author normalization remain
  unchanged. Additional fields never overwrite normalized fields.
- Cover namespaced fields, changed prefixes for the same URI, same local name
  under different URIs, case differences, repeated elements, empty/missing text,
  Unicode, CDATA, escaped HTML, mixed content, and nested descendant text.
- Cover missing/duplicate IDs, skipped entries, and input reordering so custom
  values cannot attach to the wrong article.
- Verify contains/equals/regex, any-occurrence matching, include/exclude behavior,
  unchanged `all`, immediate rule reapplication, and sibling isolation.
- Existing migrated articles retain visibility; later refreshed articles acquire
  custom values. Removed fields disappear from updated entries, failed refreshes
  preserve old data, and retention updates discovery without deleting rules.
- Fresh databases, version 1 upgrades, and recognized version 0 upgrades have
  equivalent final schemas. Preserve IDs, visibility, config revisions, extension
  data, foreign keys, rollback, concurrency, and newer-version rejection.
- Schemas 1/2 import successfully and export as schema 3. Custom rules round-trip;
  malformed IDs and unsupported schemas fail without persistent changes.
- UI and API require administration, work under plugin/proxy prefixes, escape
  hostile source text, and preserve unavailable rules across edits and imports.
- Oversized/deep/entity-bearing XML and malformed feeds follow the documented
  safety/compatibility policy without partial writes or external entity access.
- Public reader/RSS tests retain the 24-hour window, exclusion of missing
  publication timestamps, output identities, limits, GUIDs, and enclosures.

Run `uv run pytest`. If templates introduce Tailwind classes, run `npm ci` and
`npm run build:css`. Run `git diff --check` and perform a manual inspector-to-rule
walkthrough using RSS and Atom fixtures. Record unavailable checks explicitly.

## Delivery and historical outcome

- Implementation status: Implemented in the working tree on 2026-10-24.
- Delivered release/date: Assigned to 0.6.0; not published by this task.
- Related commits/PRs: No commit or pull request created by this task.
- Final database/configuration versions: Database 2 / configuration 3. Application
  and package versions are 0.6.0; `uv.lock` was regenerated with `uv lock`.
- Field identity: Implemented canonical `xml:` plus compact JSON namespace/name
  pairs, shared Unicode XML-name validation, and explicit namespace labels.
  Both normalized fields and original source elements appear in separate groups.
- Extraction/correlation: Validate size, DTD policy, and depth with Expat before
  building a DOM. Extract direct RSS 2.0 item, RSS 1.0/RDF item, or Atom entry
  children; trim outer whitespace only and preserve internal text/CDATA. Inject
  a random temporary per-entry marker into the in-memory XML before its one
  feedparser pass. Attach values through that marker, never by GUID or zipped
  list position. Markers are not stored or published. DOM serialization preserves
  prefixes, CDATA, and `xml:base`; representative normalization fixtures remain
  equivalent to direct feedparser parsing, including non-UTF-8 source encoding.
- Persistence: Added `entries.custom_fields` JSON and
  `sources.field_extraction_error`. Rebuild only `filter_rules` for the expanded
  identifier constraint; preserve IDs, sequence high-water marks, extension
  indexes/triggers/views, external references, visibility, and config revision.
  Migration 0→1 uses its frozen schema; migration 1→2 then extends it atomically.
- Availability: The source API returns `fields` with `id`, `label`, `kind`,
  `namespace`, `local_name`, and `available`, plus `extraction_error`. Its catalog
  includes normalized fields and original fields present in retained entries.
  The output editor additionally unions its own unavailable rule references.
- Concrete limits: 5 MiB source response, 100 XML levels, 5000 extracted entries,
  256 direct field elements per entry, 256 KiB extracted UTF-8 text per entry,
  and 8 MiB extracted text per source. Limit violations or DTD declarations fail
  refresh without partial entry/visibility writes; source error state is updated.
- Compatibility policy: Malformed XML retains feedparser's tolerant ingestion
  when entries remain parseable, with no custom values for those updated entries
  and an explicit source warning in overview, settings, and field API. Unsupported
  XML feed layouts or entries without safe marker correlation likewise expose
  no custom values and warn. Existing stored values survive failed refreshes,
  but successful tolerant updates clear custom values on the entries they replace.
- Configuration: Strict schemas 1 and 2 convert into schema 3. Original values
  and discovered catalogs remain runtime-only; valid unavailable custom-field
  rules survive import and remain editable before the next source refresh.
- Test/build results: `uv run pytest` passed (147 tests), including the original
  public-feed regressions, parser identity/reordering fixtures, rule/API/form
  validation, RSS/Atom inspector-to-rule workflows, migration rollback and
  concurrency, legacy config conversion, and retention/unavailable-field cases.
  `node --test tests/test_feed_tabs.cjs` passed (1 test). `npm ci` and
  `npm run build:css` succeeded; no generated stylesheet change was needed.
  `git diff --check` passed. npm reported an unapproved optional watcher install
  script; the CSS build emitted a Node deprecation warning, without failing.
- Checks not performed: Manual browser walkthrough, a live external source,
  production database migration, or release publishing. End-to-end RSS and Atom
  fixture workflows were checked with authenticated TestClient requests instead.
- Known upgrade limitation: Existing retained articles have no original-field
  values until those entries are successfully fetched again.
