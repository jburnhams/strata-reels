# Unit tests

Shared commands, layout, hermetic setup and file-ownership rules are in [`tests/README.md`](../README.md); read that first. This file is owned by unit-test work.

Fast and isolated: synthetic data, a migrated SQLite copy per test for the records; no external services, no network. `pytest tests/unit --cov` is what CI gates on (`fail_under` in `pyproject.toml`).

## Where things are tested

| Module | Tests | Coverage | Status | Notes |
| --- | --- | --- | --- | --- |
| `settings.py` | `test_settings.py` | 100% | done | the `reels` home and its variable; no settings yet, so any key is refused |
| `models.py`, `migrations/` | `test_database.py` | 100% | done | strata-python's standard tests: migrations up and down, schema matches the models |
| `system.py` | `test_system.py` | 100% | done | folder sizes (files that vanish mid-walk) |
| `cli.py`, `db.py` | `test_cli.py` | 100% | done | doctor, set refusing an unknown key, serve delegation, openapi, add and sources (text and JSON), jobs and work (a failed job exits 1) |
| `results.py` | `test_results.py` | 100% | done | provenance from strata-models' answer, what the cache key covers, the same request stored once, superseded not overwritten, spans side by side in time order, refusals |
| `overrides.py` | `test_overrides.py` | 100% | done | change is a new row, withdraw marks, active per target, refused targets, modes, scopes and no author |
| `derived.py` | `test_derived.py` | 100% | done | which assets a source gets, each a result with provenance made once, a deleted file made again, a changed recipe superseding, nothing before the hash, ingest of a known tape, a changed file refused, what the makers pass to strata-python and keep (ffmpeg stood in for by the `made` fixture; real assets in tests/integration) |
| `jobs.py` | `test_jobs.py` | 97% | done | the `hash` job (and the asset jobs it queues) in this process (once, dedupe, a changed or missing file refused, no such source), the server's runner on threads, a runner waiting for a database that will not open; job processes are in tests/integration |
| `server/app.py` | `test_server.py` | 100% | done | the committed `openapi.json`, landing/static index with its relative `<base>`, sources listed, added (201, then 200 when known) and refused (400), error bodies, system, no settings, `main` |
