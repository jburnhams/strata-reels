# Changelog

Newest first, grouped by day. One entry per change, in the same commit or PR as the change.

## 2026-11-23

**Footage in: sources in the database (branch `claude/project-thread-1p0er6`)**
The first code of milestone R1 step 1: add a tape as a source, known by its content, probed once, kept in the database. Restored copies and proxies come next, once strata-python's time model is released.
- `pyproject.toml`, `uv.lock`: `strata-python[db]>=0.2.6,<0.3` from PyPI, as strata-python's README now says apps depend on it, replacing the git source that tracked main (the lock was at a commit from before `media` and `db`); the `[db]` extra brings SQLAlchemy, Alembic and psycopg. `[tool.strata-db]` (app `reels`). Project name `strata-reels`.
- `src/app/settings.py`: settings named `reels` (home `~/.reels`, variables `REELS_*`) instead of the template's `app`, which every unrenamed template app on a machine would share; `strata_env_prefix` and the tests, `deploy/app.service`, `docs/deploying.md` and `tests/README.md` follow. The Python package is still `app`.
- `src/app/models.py`: **new**; `Source` (fingerprint, path, name, size as a big integer, ffprobe's whole answer). `migrations/20261123_92e993263160_sources.py`: **new**, generated and reviewed.
- `src/app/sources.py`: **new**; `add` (refuses a missing file, one ffprobe cannot read, or one with no video; the same content added again or after a move is the same source), `all_sources`, `as_json` (with the exact frame rate text and field order).
- `src/app/server/app.py`: `GET /api/sources`, `POST /api/sources {path}` (201 new, 200 known, 400 refused), on `strata_python.db.web.add_database` (the shared Postgres when configured, else `reels.db` in the settings folder). `src/app/cli.py`: `add PATH...` and `sources`.
- Tests: `tests/unit/test_sources.py`, `tests/unit/test_database.py` (strata-python's migration checks), `tests/integration/test_sources_ffprobe.py` **new**; API and CLI cases in `test_server.py`, `test_cli.py` and `test_server_api.py`. `.github/workflows/integration.yml` installs ffmpeg and sets `REELS_TEST_FFMPEG=1`.
- `src/app/__init__.py`, `server/landing.html`, `server/app.py`: the package name is `strata-reels`. The web app still says python-app-template: it changes when the sample screens are replaced, with the visual baselines.


**Plan the AI video editor (branch `claude/project-thread-1p0er6`)**
Jonathan asked to discuss his AI video editing spec as the basis for this app. After reading the spec, strata-360, strata-models, strata-python, strata-react and strata-vid, the plan was agreed in discussion: models (faces and recognition included) in strata-models, strata-360's lessons but none of its code, a small clean repo, family VHS tapes first. Docs only; no code changed.

- `docs/spec.md`: **new**; the original specification, unchanged, with a short header.
- `docs/overview.md`: rewritten from the template's brief to this app's: goal and the two first uses, which repository owns what, what is learnt from strata-360, departures from the spec, expected strata-react gaps. The template's principles are kept at the end. Storage says the app's models and migrations live here, on `strata_python.db`, which replaced the `jburnhams/database` client that AGENTS.md still names (a review finding).
- `docs/implementation-plan.md`: milestone R1 (a tape in, a highlight reel out) and the later ones; the template's milestones kept as done.
- `docs/progress.md`: the first entry: what was read, found and decided.
- `README.md`: title and opening paragraph describe this app; the template's setup text is unchanged.

## 2026-11-09

**Regenerate visual baselines on the CI runner (branch `claude/project-thread-iw7199`)**
Jonathan wants every dependency kept current, including Dependabot's playwright and lucide-react bumps. Those change the Chromium or the icons behind every screenshot, and a laptop cannot reproduce the CI Chromium, so the baselines are now written by a manual workflow.

- `.github/workflows/update-visual-baselines.yml`: **new**; manual run on a chosen branch: `test:visual:update`, then `test:visual` to prove stability, then commits `web/tests/visual/baselines` to that branch.
- `AGENTS.md`: CI section mentions it.

**Move onto the strata-python library (branch `claude/project-thread-iw7199`)**
Jonathan asked for the shared server code to live in a `strata-python` project (like strata-react) so the template stops duplicating it, and for apps to track its latest. This replaces the template's own copies; behaviour is unchanged except where noted.

- `pyproject.toml`, `uv.lock`: depend on `strata-python` as a git source tracking `main` (the lock pins the commit); pytest options `strata_env_prefix`/`strata_tuning_vars` configure the library's plugin.
- `src/app/oslib.py`, `scripts/check.py`, `tests/utils/http_server.py`, `tests/unit/test_oslib.py`, `tests/unit/test_http_server.py`: **removed**, now `strata_python.oslib`, `strata-check` and the plugin's `http_server` fixture (the library tests them, with real processes and sockets).
- `src/app/settings.py`: declares the two settings with `strata_python.settings.Settings`; same files and `APP_*` variables. Corrections: a bad `data_dir` or blank greeting now raises ValueError from `save` (the API answers 400, as it did for a relative path), `~` in `data_dir` is expanded when saved, and a bad stored or environment value falls back to the default instead of crashing.
- `src/app/server/app.py`, `src/app/cli.py`: health, settings and page routes, `serve`, `doctor` and `set` come from the library; the app keeps its own routes and `hello`/`messages`.
- `tests/conftest.py`: hermetic environment, no-network guard and `http_server` fixture removed (plugin). The `fake_*` fixtures stay as last resorts.
- `scripts/check`, `.github/workflows/unit.yml`: run `strata-check`. `.github/workflows/upgrade-strata-python.yml` **new**: weekly lock upgrade that runs the gate and opens a PR.
- `AGENTS.md`, `README.md`: describe the library, the tracking-main rule and the upgrade workflow.

**Testing policy: real code over mocks (branch `claude/project-thread-iw7199`)**
Asked to steer tests away from mocks towards real HTTP, embedded databases and installed tools, and to make skipping deliberate.

- `AGENTS.md`: **new** Testing policy section (real by default; mocks only for true externals in quick many-case unit tests; install ffmpeg and the like instead of mocking; no silent skips, a skip is a marker with a reason and a switch; machine-heavy tests are marked and run where the machine is).
- `tests/utils/http_server.py`, `tests/unit/test_http_server.py`: **new**. A real loopback HTTP server scripted per test (`http_server` fixture in `tests/conftest.py`); a request no route answers fails the test at teardown.
- `tests/utils/fakes.py`: docstring marks the doubles as a last resort. `tests/README.md`: layout notes point to the policy.
- `pyproject.toml`: ruff `TID` rules selected, `pytest.importorskip` banned with the reason.

**AGENTS.md reorganised as the basis for apps made from the template (branch `claude/project-thread-iw7199`)**
Asked to make the agent instructions suitable as the starting point for future apps, and to point database work at the shared database project.

- `AGENTS.md`: regrouped into rules for every change (run `scripts/check`, changelog and docs in the same commit, comment the why, no secrets or machine-specific details, strict types, tests, report what was run), layout, Python tooling, tests, web, CI and a closing checklist. Says which parts to keep unchanged in a derived app. Content of the earlier bullets is kept.
- `AGENTS.md`: **new** Database section. Apps use [jburnhams/database](https://github.com/jburnhams/database) (one shared database, one schema per app, models and migrations in the app repo, `dbtool check` in CI, never one database per app) and link its adoption guide rather than repeating it.

**Generic deployment notes and a systemd unit example (branch `claude/project-thread-iw7199`)**
A Docker image was dropped from the plan because apps are not deployed that way. These files are deliberately free of machine-specific details.

- `deploy/app.service`: **new**. Example systemd user unit running `uv run --locked --no-dev app serve`.
- `docs/deploying.md`: **new**. Build, install, run, reverse-proxy and how to keep a service file in step with the repo.
- `AGENTS.md`: points to them; the stale `setup_env` mention is gone (corrects the earlier line left behind by the uv change).

**Move the Python side to uv, typed strict, one gate (branch `claude/project-thread-iw7199`)**
Asked for an evaluation of this template against current best practice; the tooling gaps it found are closed here. Behaviour of the app is unchanged.

- `pyproject.toml`: now a real project (`[project]`, hatchling build, `app` console script, `[dependency-groups] dev`), with `--strict-markers --strict-config` for pytest, ruff rules `E,F,I,B,UP,SIM,RUF,C4,PT,S` at line length 120, and pyright strict.
- `uv.lock`, `.python-version`: **new**. Builds are reproducible; CI installs with `uv sync --locked`.
- `requirements.txt`, `requirements-test.txt`, `scripts/setup_env.sh`, `scripts/setup_env.ps1`: **removed**; `uv sync` replaces them. `app` and `app.cmd` stay as thin wrappers over `uv run app`.
- `scripts/check`, `scripts/check.py`: **new**. The one gate (lockfile, ruff lint and format, pyright, ty, unit tests with coverage) for git hooks, agents and CI.
- `src/app/`: fully annotated; `__version__` now reads the package metadata. `oslib.py` relaxes pyright for that one file because its Windows branches use APIs the Linux stubs lack. Formatting is `ruff format` (separate commit, no behaviour change).
- `tests/`: new `test_version.py` and a partial-settings test (coverage is 100% again); a few asserts split for ruff's PT018.
- `.github/workflows/unit.yml`, `integration.yml`: uv with cache; lint, format and types run once on Linux 3.13; weekly `uv audit`. `.github/dependabot.yml`: **new** (uv, npm, actions).
- README, AGENTS.md, test READMEs, docs: commands updated to uv and `scripts/check`.
- Not done yet: Dockerfile and systemd unit, copier conversion, release workflow (next phases).
