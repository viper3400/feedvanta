# Agent instructions

## Project

- FeedVanta is a Python 3.11+ FastAPI application that reads RSS/Atom feeds,
  stores entries in SQLite, applies filters, and publishes generated RSS feeds.
- Follow the existing project structure and style. Keep changes focused and
  avoid adding dependencies unless they are necessary.
- Preserve public generated-feed behavior and handle feed publication timestamps
  consistently. Generated feeds include only visible entries published within
  the last 24 hours; entries without a publication timestamp are not included.
- If a change requires a database schema update, include the corresponding
  migration/initialization change and tests.

## Tests

- Run the test suite with `python3 -m pytest` when possible.
- Add or update tests for behavior changes, including relevant boundary cases.
- If tests cannot be run, state why rather than implying they passed.

## Commits

- Use Conventional Commit messages: `<type>: <imperative summary>` (for example,
  `fix: limit generated feeds to 24 hours`).
- Keep each commit focused and include related tests and documentation changes.
- Stage only files relevant to the requested change; do not include unrelated
  working-tree changes.
- Report test results and any checks that could not be run when summarizing the
  commit.
