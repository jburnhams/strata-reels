# Design brief

## 1. Goal

An AI-guided video editor: it ingests footage, works out what is in it, and proposes a complete, editable cut, which a person (or an agent) then corrects. Manual editing is always possible, but the normal path is "the system proposes, you override". The source of the design is the [original specification](spec.md); this brief records how it is being built and where it departs from the spec.

Two uses come first (Jonathan, 2026-11-23):

1. **Family videos**: digitised VHS tapes. Hours of flat, interlaced PAL footage per tape, little planned speech, many short scenes, unknown dates. The first milestone is built on these.
2. **Race videos**: 360° clips shot during ultramarathons, today handled by [strata-360](https://github.com/jburnhams/strata-360). Longer term strata-reels replaces strata-360; 360 footage returns as a second kind of source once the core works on flat video. strata-360 was research: its lessons carry over, its code does not (section 3).

Not a full nonlinear editor (spec §1): no grading, compositing, motion graphics or real-time collaboration.

## 2. Where each part lives

| Repository | What it owns here |
|---|---|
| [strata-models](https://github.com/jburnhams/strata-models) | **Every model call**, including face detection and recognition in full (who a face is, across tapes and apps) and restoration. strata-reels asks for a role (`transcribe`, `faces`, `vision-language`, `video-enhance`, ...) and never loads a model or keeps its own face identities. strata-models runs the evals and wires the best model to each role, so a better model is one change there. Roles strata-reels needs that it does not serve yet (shot boundaries first; then speaker diarization, word alignment, sound events, speaker embeddings, translation) are added there, each with an eval and a simple baseline. |
| [strata-python](https://github.com/jburnhams/strata-python) | Shared server code: `media.probe`/`ffmpeg`, the durable job queue (`tasks.queue`), the database machinery (`strata_python.db`: connection to the shared Postgres, Alembic without a per-app `env.py`, test fixtures), and the strata-models client (`clients.models`). Generic pieces this project needs are added there rather than here: an exact time model (integer ticks over a declared timebase, ranges, source-to-timeline maps), proxies, thumbnail strips and audio peaks beside `probe`, and a cached, resumable stage runner if strata-360's generalises cleanly. |
| [strata-react](https://github.com/jburnhams/strata-react) | Every reusable UI part, built as generic components. Most already exist (`Timeline`, `SequenceTimeline`, `TranscriptView`, `MarkableText`, `MediaPlayer` with spans, `Waveform`, `PairVote`, `CompareVideo`, `RegionEditor`, `BoxOverlay`, `Inspector`, `Workspace`, `useHistory`, `JobStatus`). Gaps are raised there as issues, not hand-built here (section 5). |
| strata-reels (this repo) | Only the editing domain, kept small and clean from the start rather than one project that does everything: sources and derived assets, analysis results with their provenance, scenes and candidates, the proposal and the person's overrides, the planner, its own database models and Alembic migrations, the ffmpeg render, OpenTimelineIO export, and later the MCP server. Anything a second app needs moves to strata-python. |
| [strata-vid](https://github.com/jburnhams/strata-vid) | A browser editor (WebCodecs) already ported onto strata-react. A source of ideas and of components to generalise into strata-react (its compositor is the starting point for the edit-list player), not a dependency. Its float-seconds time model and its store are not reused. |

## 3. What is learnt from strata-360

strata-360 tried the spec's core ideas on one kind of footage. It was research-oriented, so **no code is copied or ported from it** (Jonathan, 2026-11-23): strata-reels is written fresh, well structured from the start. These are the ideas that proved themselves there (its `docs/overview/` sections 3, 5, 6, 15, 16 and 18) and are designed in here:

- **The language model never edits video.** It reads text and stills and returns structured JSON; everything else is deterministic code (strata-360 §3, spec §5).
- **Options, not decisions, early.** Analysis records what is possible (candidate windows with handles and safe cut points), and choices are made as late as the information allows.
- **The edit is a diff on the proposal.** The project holds the automatic proposal and the person's overrides separately: locks, pins, bans, forced choices, trims, preferences. The proposal is a pure function of (analysis, overrides, seed), so re-planning never loses a manual choice and an override whose window has gone is reported, not dropped (strata-360 §18.3 and §18.4n; spec §1 invariants, §6).
- **Corrections are layered.** A model's output is never changed in place: corrections sit above it with their source (strata-360's `transcript_edits.json`; spec §3 "observations ... user corrections ... stored separately").
- **Everything is cached by content hash plus parameters**, and stages are resumable and retried with backoff (strata-360 §3, `pipeline/retry.py`; spec §6).
- **Time is exact.** Source, project and render time are distinct, with explicit transforms (strata-360 §6, spec §3 "Time correctness").

## 4. Departures from the spec, and why

- **Storage**: Postgres, as the spec recommends: one shared database with a schema for this app, the app's typed models and reviewed migrations in this repo, the machinery from `strata_python.db` (which took over the `jburnhams/database` client, `omarchy_db`; strata-python's DEVLOG has the move; the Database section of [AGENTS.md](../AGENTS.md) has the rules), plus files on disk for media and heavy artifacts. No vector index or columnar files until a stage needs them.
- **Models**: the spec's per-stage model research (S1 to S7) happens in strata-models' eval framework, not here. strata-reels records which role and model produced each result, so a model change can be traced and re-run.
- **First milestone**: the spec's M0 is foundations only. Here the first milestone is a thin end-to-end slice on real footage (a tape in, a highlight reel out) that builds only the foundations that slice needs, because the spec's own risk register warns against building infrastructure before showing that automatic selection makes good edits.
- **Order of analysis**: for home video, scenes and "what is happening" come before speech; the spec's transcript-first baseline still exists, as one signal among several.

## 5. Gaps expected in strata-react

1. **An edit-list player**: plays a list of source ranges from several files, gapless, without rendering. `MediaPlayer` plays whole files in turn.
2. **Proposal review**: the difference between two revisions, each change accepted or rejected, with its reason and evidence.
3. **A crop path with keyframes over time**: `RegionEditor` has a start and an end only. Needed for reframing (later milestones).

Each is raised as a strata-react issue when the milestone that needs it starts.

## 6. The data model

Written down before R1 step 2 stores its first analysis result, because every later step reads these records and the spec's invariants (§1, §3) are cheapest to keep from the first row. Each is a table in this app's schema; heavy payloads (frame-wise signals, thumbnails, proxies) are files on disk referenced from the row (as built, in a folder named by the result's cache key, which is itself a hash of the inputs' content and the request).

Built in R1 step 1b: `Source.sha256` and the `hash` job, `Result` (`results.py`), `Override` (`overrides.py`) and the job queue (`models.JOBS`, `jobs.py`). Derived assets from the raw footage (proxy, thumbnails, peaks) are built in step 1 as results with a file (`derived.py`); the restored tape comes in step 7; revisions and renders with the steps that make them. Step 2 adds results of kind `shots` (strata-models' `shot-boundaries`, by frame), `keyframes` (a derived asset: one picture per shot), `embeddings` (`image-embed` vectors of the keyframes, in a file) and `scenes` (this app's own grouping, role `reels-scenes`, keyed on the results it read); each names the result it was made from in its parameters, so a re-run upstream makes new ones downstream. Step 3 adds `transcript` (strata-models' `transcribe` over the whole source, doubtful segments marked), `faces` (`faces` on every keyframe, boxes and scores, no embeddings until R2's consent) and `description` (one per scene, its span the scene's: `scene-describe` with preset `scene-describe-v1`, observed facts and inferences in separate fields), each keyed on the results it read.

- **Source**: a file of footage as added, never changed (built: `models.Source`). Found by its quick fingerprint (size and both ends, milliseconds on a tape); also known by a **full SHA-256** of its content, computed by a background job after it is added (it reads the whole file, checked against the fingerprint before and after, so a file changed under the same path is refused), which is its identity in every cache key and render manifest (spec §3 MediaAsset: "cryptographic hash").
- **Derived asset**: a file made from sources (a browser proxy, audio peaks, thumbnails; later the restored tape from `video-enhance`), with its own hash and the same provenance as a result. Never edited in place; a new parameter makes a new one. As built, a derived asset is a `Result` with a `file` (role `ffmpeg`, the recipe and its version in the parameters, the file's SHA-256 in the payload), stored under `derived/<cache key>/` in the app's home, so the same supersession and cache rules hold without a second table.
- **Result** (the spec's EvidenceArtifact): what one analysis said about one source or derived asset: kind (`shots`, `transcript`, `description`, `faces`, ...), the span of source time it covers (`mediatime` spans, exact), the payload or a file reference, confidence where the producer gives one, and its provenance: role, model, a hash of the model's settings, the strata-models job id, and the hashes of its inputs. Append-only: a re-run with a different model or settings writes a new result and marks the old one superseded; nothing is overwritten. Its cache key is hash(inputs + role + model + settings hash + parameters) (spec §6), with the kind and the span asked about among the parameters. Observations ("a cut at 12.04 s", "faces: 2") and inferences ("a birthday party") are separate fields or separate results, never one sentence (spec S4). As built, a result supersedes the current one for the same source, kind and span when its cache key differs, and the same key again returns the stored row; its span is two exact rational texts (`mediatime.text`).
- **Override**: something the person said, stored apart from the results it corrects and never written into them: a corrected boundary or description, a name for a face, a lock, pin or ban on a scene, a preference for the planner. Each has a target (a result, a scene or a clip), its scope, a mode (advisory, prefer, require, forbid: spec §3 Constraint) and who made it and when.
- **Revision**: an immutable version of the reel (ordered clips, each a source range with its own id, its `TimeMap`, trims, gain) and the planner inputs that produced it (results used, overrides, seed, planner version). A **proposal** is a revision not yet accepted, with its base revision; accepting one is compare-and-swap on that base, so a stale proposal becomes a conflict, never a silent overwrite (spec §3 Proposal, §6). Locked choices that a new proposal cannot keep are reported as conflicts.
- **Render**: a render manifest (revision, input hashes, ffmpeg and filter versions, output checksum: spec S8).
- **Job**: analysis and renders run as queued, resumable jobs on `strata_python.tasks.queue`, keyed by the result's cache key, so a finished one is not run again.

The edit model is ours (rows above), with OpenTimelineIO as an export, not the store: the spec's open decision 1, taken for R1 because provenance and overrides need fields OTIO does not have; OTIO's round-trip loss is reported when export is built.

## 7. The app skeleton

The repository was made from [python-app-template](https://github.com/jburnhams/python-app-template): a FastAPI server and CLI in `src/app/` on strata-python, and a React app in `web/` on strata-react that builds into `src/app/server/static/`. The template's sample (greetings, a paged log, sample settings) was left out; the screens are Sources, System and Settings (appearance only). Conventions are in [AGENTS.md](../AGENTS.md).

## 8. Principles kept from the template

- **Hermetic tests by default**: every test run gets an isolated home and data directory, stripped environment overrides and a socket guard against outbound network calls. strata-models is reached through the plugin's `fake_models` in unit tests and a real service only where a test is marked for it.
- **Two-tier test suites** (unit and integration, Python and web) with ratcheting coverage floors.
