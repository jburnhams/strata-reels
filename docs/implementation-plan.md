# Implementation plan

What to build next, in order. Design and reasons: [overview.md](overview.md). Source: [spec.md](spec.md). Each milestone is agreed with Jonathan before it starts; new dependencies (for example `opentimelineio`, the MCP Python SDK) need his decision first.

## R1: a tape in, a highlight reel out (family VHS)

Goal: one digitised VHS tape becomes a list of scenes a person can browse and an automatic highlight reel of a chosen length, which they can correct and render. Everything after ingest runs as queued, resumable jobs.

1. **Sources and derived assets.** *(Sources done 2026-11-23: `app add`, `/api/sources`.)* Add a tape: hash, `media.probe`, store as an immutable source. Ask strata-models' `video-enhance` for the restored version and store it as a derived asset that records the role, model and parameters that made it. Make a browser proxy (deinterlaced, short GOP) and audio peaks. Needs: the time model, proxies and peaks in strata-python.
2. **Scenes.** Split the tape into shots and group them into scenes. Needs a shot-boundary role in strata-models with an eval; ffmpeg's scene score is the baseline to beat. Each scene keeps its boundary confidence (spec S5).
3. **Per-scene analysis.** A thumbnail strip; a transcript of any speech (`transcribe`); a short description of what is happening (`vision-language` on sampled frames, observed facts kept apart from guesses, spec S4); the faces present (`faces`). Every result records its role, model and inputs.
4. **The tape's chapters.** A screen listing the scenes with thumbnails, descriptions and length; play any scene; correct a boundary or a description (stored as an override, not an edit of the model's output).
5. **The highlight proposal.** Given a target length, choose and trim scenes into a reel. A deterministic baseline first (scores from description, faces, speech and picture quality), the language-model planner after. The proposal is a pure function of (analysis, overrides, seed); locks, pins and bans survive re-planning.
6. **Review and preview.** The reel on a timeline with gapless preview of the edit (the strata-react edit-list player), swap or trim a scene, ask for "another version".
7. **Render and export.** ffmpeg render of the reel from the restored asset, with a render manifest (revision, inputs, versions, checksum); OpenTimelineIO export.

Done when: a real tape goes through all seven steps on the GPU machine, the reel is watchable without manual fixes beyond overrides, and the time model's round-trip tests pass (source to timeline to source, frame-exact on 25 fps interlaced PAL).

## Later (order to be agreed)

- **R2: people.** Who is in each scene, from strata-models' face recognition (identities live there, shared with the other apps, not here); "every scene with Grandma" as a pin.
- **R3: race footage.** 360° clips as a second kind of source, written fresh from strata-360's lessons (no code ported); heavy 360 processing such as stitching lives outside this repo, as restoration does. strata-360 retires once reels does what it does.
- **R4: the agent loop.** MCP server over the same API as the UI (spec §7): search, inspect, propose, validate, commit with compare-and-swap on the revision.
- Speaker diarization, sound events, music and voice-over (strata-360 §17), reframing to 9:16 (spec S7), as strata-models gains the roles.

## Template milestones (done before this project started)

- [x] **M0 scaffold**: repository layout, `uv sync` setup (replaced the earlier `setup_env` scripts on 2026-11-09), `./app` and `app.cmd` launchers, `oslib.py`, `settings.py`, `doctor` CLI command.
- [x] **M1 hello-world backend & CLI**: `greet.py` domain module, `hello` and `messages` CLI commands, FastAPI server (`server/app.py`) with `landing.html` fallback.
- [x] **M2 React web app**: Vite + React 19 + Tailwind CSS v4 frontend (`web/`), hash router (`route.ts`), reverse-proxy-safe API client (`api.ts`), greeting and settings pages.
- [x] **M3 test suites & CI**: hermetic pytest unit and integration suites, Vitest unit (node) and integration (jsdom + msw) suites, coverage gates, and GitHub Actions workflows (`unit.yml`, `integration.yml`).
- [x] **M4 web app on strata-react**: the frontend rebuilt on [strata-react](https://github.com/jburnhams/strata-react) as a worked example for new apps, with Storybook and `strata-visual` screenshot tests in CI.
