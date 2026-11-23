# Changelog

Newest first, grouped by day. One entry per change, in the same commit or PR as the change.

## 2026-11-23

**The records: results, overrides, jobs and the content hash (branch `claude/project-thread-ui8f4c`)**
R1 step 1b, which Jonathan agreed: the tables every later step writes into (overview §6) and the job runner, before step 2 stores its first result. strata-models #103 (each job's model and settings hash, returned by the client) is done in strata-models PR #104, so this side only takes its answer.
- `src/app/models.py`: `Source.sha256` (the full content hash, None until the `hash` job has read the file); `JOBS`, strata-python's queue table in this app's schema; `Result` **new** (kind, source, exact span, role, model, settings hash, the model's options, the strata-models job, input hashes, parameters, cache key, payload or file, confidence, quality flags, the reels job, `superseded_by`); `Override` **new** (target kind and id, kind, value, mode, scope, author, reason, `superseded_by`, `withdrawn`). `src/app/migrations/20261123_ae4455112b76_records.py`: **new**, generated and reviewed.
- `src/app/results.py`: **new**. `Provenance` (`of(role, served)` takes strata-models' `{model, options, settings_hash, job_id}`), `cache_key`, `record` (the same key returns the stored row; a different request for the same source, kind and span supersedes the current one in the same transaction; refuses a result with no payload or file, no inputs, or a confidence outside 0 to 1), `current`, `find`, `history`, `span`.
- `src/app/overrides.py`: **new**. `add`, `change` (a new row superseding the old), `withdraw`, `holds`, `active`; modes are the spec's advisory, prefer, require and forbid.
- `src/app/jobs.py`: **new**. The `hash` kind (the file is checked against the source's fingerprint before and after it is read, so a file changed or replaced under the same path is refused), `hash_later`, `work` (run what is queued in this process), `Runner` (the server's `Worker`, started once the database opens and retried while it cannot).
- `src/app/server/app.py`: the server runs the queue while it is up (`create_app(run_jobs=...)`: job processes by default, threads or off for the unit tests); `POST /api/sources` queues the hash of a source that has none. The API's shape is unchanged.
- `src/app/cli.py`, `src/app/db.py`: `add` queues each new source's hash; `sources` shows the first 12 characters of the hash or "(hashing)"; `jobs [--json]` and `work` **new**; `db.opened()` for a command that needs the engine.
- `pyproject.toml`, `uv.lock`: `strata-python[db]>=0.2.7,<0.3` (0.2.7 has `mediatime`, which the results' spans use). Its responses now mark defaulted fields required, so `web/openapi.json` and `web/src/api.generated.ts` are regenerated (`Folder`, `ServiceStatus`, `SystemInfo`) and `web/tests/unit/logic.test.ts` gives a folder its two byte counts. `AGENTS.md` names the new floor.
- Tests: `tests/unit/test_results.py`, `test_overrides.py`, `test_jobs.py` **new**; `test_cli.py` (jobs and work), `tests/conftest.py` (the `client` fixture runs no jobs; the engines a handler opened are disposed after each test); `tests/utils/probes.py` **new**, the sample ffprobe answer moved out of `test_sources.py` so other tests can use it; `tests/integration/test_cli.py` (add, work, hash) and `test_server_api.py` (the live server hashes a new tape in a job process). `tests/unit/README.md`: ledger rows.
- Docs: `docs/overview.md` §6 says what is built and how supersession works; `docs/implementation-plan.md` marks step 1b done; `docs/progress.md` entry; `README.md` and `AGENTS.md` list `jobs` and `work`.
- Not done here: an API or screen for jobs (with step 2's first long job, on the System page), derived assets, revisions and renders. Hashing reports no progress: `files.sha256_hex` has no hook for it.

**Plan: checked against the spec, data model written down (branch `claude/project-thread-1xqj2q`)**
Jonathan asked whether the work is on track against the spec. It is; the gaps found are the spec's provenance and record-keeping invariants, which are cheapest to build in before R1 step 2 stores its first result, so he agreed to put them in the plan.
- `docs/overview.md`: new section 6, the data model (source with a full SHA-256, derived asset, result with provenance and supersession, override, revision and proposal with compare-and-swap, render manifest, job), and the decision that the edit model is ours with OpenTimelineIO as an export (spec §10 decision 1). Sections 6 and 7 renumbered to 7 and 8.
- `docs/implementation-plan.md`: step 1 adds the background full hash (the fingerprint samples only the ends, as its docstring says, which the plan's "hash" did not); new step 1b, the records and job runner, needing strata-models to report each job's settings; step 2 runs `shots-spliced` on real footage and adds a check on tape faults; R2 adds opt-in, deletion and consent for face recognition.
- `docs/progress.md`: the check and what it found.

**Update from python-app-template, without its sample (branch `claude/project-thread-1p0er6`, PR #7)**
Jonathan asked for reels to be brought up to the latest template, then for nothing that is not reels to come with it. Reels was made from template commit `d0793de`; this applies the template's changes since then (`d0793de..3863362`: strata-python 0.2.6, typed routes and generated API types, the system page, CI's strata-db, public and Postgres checks, the web on strata-react 1.3.3 with Biome and oxlint) and keeps reels' own work on top. The template's sample (greetings in a database, a paged log, a greeting word and style as settings, and their screens, tests, stories and baselines) is left out, along with what reels had kept of it (`greet.py`, `hello`, `messages`).
- Taken from the template: the CI workflows (the weekly strata-python upgrade workflow is gone: dependabot groups uv), `AGENTS.md` (which now describes `strata_python.db`, so the overview's note that it named the old database client is removed), `src/app/{db,system}.py`, the test fixtures, and the web app's frame (`Layout`, `SystemScreen`, `NotFoundScreen`, mocks, stories, visual config). `README.md`, `AGENTS.md`, the test READMEs and `docs/overview.md` describe reels' screens and commands instead of the sample's.
- Settings: `src/app/settings.py` is `Settings("reels", [])`, so `REELS_HOME`, `REELS_DATABASE_URL` and `~/.reels/reels.db`, and no settings of its own yet (`strata_tuning_vars = []`); the template dropped `data_dir` (records live in the app's home). `deploy/app.service`, `docs/deploying.md` and `tests/README.md` follow.
- Database layout as the template's: `src/app/strata-db.toml` (app `reels`) and `src/app/migrations/`, so both ship in the wheel. The `sources` revision (moved from `migrations/`, retyped for strict pyright) stays the first and only one. `src/app/models.py`: `Source` only.
- `src/app/sources.py`: answers are typed (`Described`, `Video`, `contracts.ApiModel`), so `/api/sources` is in `web/openapi.json` and `web/src/api.generated.ts`; a `SourceError` is a ValueError, answered 400 by the shared error handlers; `count` for the system page. `src/app/system.py`: the page's own number is `sources`. `server/app.py`, `cli.py`: the sources routes and the `add`/`sources` commands on the template's shape (`db.session()`). `server/landing.html`: the stand-in lists sources and adds one.
- `web/src/screens/SourcesScreen.tsx`: **new**, the index route. Footage in a `DataTable` (name, length, picture from ffprobe's size, frame rate and field order, size, added; sort in the URL), the finding in a sentence (how many, how long in all), and a one-field `Field action=` form that adds a file by its path, with the server's refusal under the field. `SettingsScreen`: appearance only. `api.ts`: `Source`, `Video`, `keys.sources`. `mocks/`: five sample sources and their states. `index.html`, `package.json`: named `strata-reels`.
- Tests: `tests/unit/test_settings.py`, `test_server.py`, `test_cli.py`, `integration/test_cli.py` and `integration/test_server_api.py` (live server, real ffmpeg) rewritten around sources; `test_greet.py` removed. Web: `tests/integration/SourcesScreen.test.tsx` **new**, `SettingsScreen`, `SystemScreen`, `App` and `unit/logic.test.ts` follow. `stories/sources.stories.tsx` **new**.
- Visual baselines: the greetings, log and sample settings shots are removed; the sources and changed settings and system shots are written by CI's `update-visual-baselines.yml`, as the local Chromium does not match the pinned one.
- Not changed: `web/src/styles/theme*.css` still carry the template's palette and faces (chosen for greetings); a palette from reels' own subject is a later change.

**Plan: how shots are measured, and subjective cuts (branch `claude/project-thread-1p0er6`)**
Jonathan: measure the shot detector on clips spliced at known points (cuts within one file are the hard ones), not on cuts marked by hand, and treat where to cut inside a shot as its own question, scored and judged A/B by people.
- `docs/implementation-plan.md`: R1 step 2 names the eval `shots-spliced` in strata-models; step 6 says trims inside a shot are subjective cuts, for a later strata-models role `cut-points`, and snap to phrase ends until then.

**Footage in: sources in the database (branch `claude/project-thread-1p0er6`)**
The first code of milestone R1 step 1: add a tape as a source, known by its content, probed once, kept in the database. Restored copies and proxies come next, once strata-python's time model is released.
- `pyproject.toml`, `uv.lock`: `strata-python[db]>=0.2.6,<0.3` from PyPI, as strata-python's README now says apps depend on it, replacing the git source that tracked main (the lock was at a commit from before `media` and `db`); the `[db]` extra brings SQLAlchemy, Alembic and psycopg. `[tool.strata-db]` (app `reels`). Project name `strata-reels`.
- `src/app/settings.py`: settings named `reels` (home `~/.reels`, variables `REELS_*`) instead of the template's `app`, which every unrenamed template app on a machine would share; `strata_env_prefix` and the tests, `deploy/app.service`, `docs/deploying.md` and `tests/README.md` follow. The Python package is still `app`.
- `src/app/models.py`: **new**; `Source` (fingerprint, path, name, size as a big integer, ffprobe's whole answer). `migrations/20261123_92e993263160_sources.py`: **new**, generated and reviewed.
- `src/app/sources.py`: **new**; `add` (refuses a missing file, one it may not read, one ffprobe cannot read, or one with no video; the same tape added twice at once is one source, the second request getting the first's; the same content added again or after a move is the same source), `all_sources`, `as_json` (with the exact frame rate text and field order).
- `src/app/server/app.py`: `GET /api/sources`, `POST /api/sources {path}` (201 new, 200 known, 400 refused), on `strata_python.db.web.add_database` (the shared Postgres when configured, else `reels.db` in the settings folder). `src/app/cli.py`: `add PATH...` and `sources`.
- Tests: `tests/unit/test_sources.py`, `tests/unit/test_database.py` (strata-python's migration checks), `tests/integration/test_sources_ffprobe.py` **new**; API and CLI cases in `test_server.py`, `test_cli.py` and `test_server_api.py`. `.github/workflows/integration.yml` installs ffmpeg; the real-ffprobe tests fail, never skip, where it is missing.
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
