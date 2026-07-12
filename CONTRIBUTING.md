# Contributing to Kairos

Thanks for your interest! Kairos is in its first public phase — issues and PRs welcome.

## Development setup

See the **Development** section of the [README](README.md). In short: the API
and worker live in `apps/api` (Python/FastAPI, venv + `requirements.txt`), the
dashboard in `apps/dashboard` (Next.js 14), and the SDK in
`packages/sdk-python` (Poetry layout, `pip install -e ".[langchain]"` works).

## Tests

- Unit tests (no credentials needed): `cd packages/sdk-python && pytest`
- Integration suites (`tests/phase_*.py`, `tests/hardening_test.py`) run
  against **real** Supabase/Upstash projects configured in `apps/api/.env`;
  they create and clean up their own fixtures. Run them before submitting
  changes that touch the API, worker, or dashboard.
- CI runs SDK unit tests, an API compile/import check, and a production
  dashboard build on every PR.

## Guidelines

- Match the surrounding code style; keep changes scoped.
- Schema changes go in a new numbered file under `apps/api/db/migrations/`
  and must be reflected in ARCHITECTURE.md's v1.1 addendum section.
- Never commit credentials — `.env*` files are gitignored; `.env.example`
  files must contain placeholders only.
