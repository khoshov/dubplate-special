# Repository Guidelines (docker-first)

## Project Structure and Module Organization
- `apps/` holds Django apps such as `accounts`, `core`, `orders`, and `records`.
- `config/` contains project settings and configuration modules.
- `tests/` is the pytest root; `pytest.ini` is used (mounted into container).
- `locale/` stores translations; `media/` and `static/` are runtime assets.
- Root: `manage.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`.

## Tooling / Versions (source of truth: pyproject.toml)
- Python: 3.13 (requires `>=3.13,<3.14`)
- Django: 6.x (`django>=6.0`)
- DRF: 3.16+ (`djangorestframework>=3.16.1`)
- Dependency manager inside container: `uv`
- Lint/format: Ruff
- Typing: mypy (strict for core modules)

## Docker-first workflow

### Start/stop
- Start stack: `docker compose up --build`
- Stop: `docker compose down`

### App commands (run inside container)
Use the `django` service for all management/dev commands:
- Shell: `docker compose exec django bash` (or sh)
- Runserver: (compose already runs) `uv run manage.py runserver 0.0.0.0:8000`
- Django commands:
  - `docker compose exec django uv run manage.py migrate`
  - `docker compose exec django uv run manage.py makemigrations <app>`
  - `docker compose exec django uv run manage.py createsuperuser`
  - `docker compose exec django uv run manage.py shell`

### Tests (not primary, but available)
- Run pytest in container: `docker compose exec django uv run pytest`
- Prefer docker-based test runs for parity with services (Postgres).

## Coding Style and Naming Conventions
- Python uses 4-space indentation; keep Django app code under `apps/<app_name>/`.
- Ruff:
  - `docker compose exec django uvx ruff check --fix apps config`
  - `docker compose exec django uvx ruff format apps config`
- Keep new code typed; follow mypy policy from pyproject.

## Typing (mypy policy summary)
- Strict typing expected in:
  - `config.*`
  - `apps.accounts.*`, `apps.core.*`, `apps.orders.*`, `apps.records.*`
- Exclusions include tests, migrations, pgdata, media (see pyproject for exact list).

## Configuration and Secrets
- Copy `.env.example` to `.env` for local development.
- Keep secrets out of Git. Postgres is provided by compose.

## Language / Communication (Codex)
- **All assistant messages must be in Russian**: explanations, plans, status updates, summaries, TODO lists, and suggested commit/PR text.
- Source code identifiers follow project conventions (typically English for code), but user-facing explanations from the assistant remain Russian.
- If you must add/modify user-facing strings in the app, keep them consistent with the existing localization approach (see `locale/`).

## Codex workflow expectations
- Before making changes: provide a short plan (in Russian) describing what files/areas will be touched.
- After making changes: list modified files and provide docker commands to verify (migrate/test/ruff/mypy when applicable).
- Prefer minimal, reviewable diffs. Avoid unrelated refactors unless explicitly requested.
- If a change impacts migrations/data, call it out first and take the safest path (small migrations, clear rollback notes if relevant).

## i18n / Translations
- When adding/changing user-facing strings, consider `locale/` and avoid breaking translations.
- If new strings are introduced, update translation files according to the project’s established process.
