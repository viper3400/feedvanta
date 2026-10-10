# Agent instructions

## Project

- FeedVanta is a Python 3.11+ FastAPI application that reads RSS/Atom feeds,
  stores entries in SQLite, applies filters, and publishes generated RSS feeds.
- Follow the existing project structure and style. Keep changes focused and
  avoid adding dependencies unless they are necessary.
- Use `uv` for environment and dependency management. When dependencies change,
  update and commit `uv.lock` with `uv lock`; do not edit the lockfile by hand.
- Preserve public generated-feed behavior and handle feed publication timestamps
  consistently. Generated feeds include only visible entries published within
  the last 24 hours; entries without a publication timestamp are not included.
- If a change requires a database schema update, include the corresponding
  migration/initialization change and tests.

## Tests

- Run the test suite with `uv run pytest` when possible.
- Add or update tests for behavior changes, including relevant boundary cases.
- If tests cannot be run, state why rather than implying they passed.

## Change log

- Update `CHANGES.md` for user-facing changes, including behavior changes and
  data-retention effects.
- Keep entries concise and factual. Describe breaking impacts directly; do not
  use conversational yes/no wording or an “unreleased” label.
- Do not list version-number bumps as change-log entries.

## Commits

- Use Conventional Commit messages: `<type>: <imperative summary>` (for example,
  `fix: limit generated feeds to 24 hours`).
- Keep each commit focused and include related tests and documentation changes.
- Stage only files relevant to the requested change; do not include unrelated
  working-tree changes.
- Report test results and any checks that could not be run when summarizing the
  commit.
