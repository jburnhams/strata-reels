# Original specification

Jonathan's spec for the project, as uploaded on 2026-11-23 and stored unchanged below. It is the source the plan in [overview.md](overview.md) and [implementation-plan.md](implementation-plan.md) was built from; where the plan departs from it, the overview says so and why.

---

# AI-Native Video Editing Engine
## Technical specification and experimental research programme
**Version:** 0.1 · **Date:** 2026-11-17 · **Status:** proposed architecture; model choices subject to evaluation

## 1. Objective and scope
Build an automated, non-destructive, provenance-aware editing engine that ingests video/audio and supporting material, derives a searchable semantic understanding, proposes narrative assemblies, clip selections, cuts and virtual-camera moves, and permits human or agent intervention at every stage. Its primary deliverable is a versioned, editable timeline plus reproducible preview/render. It is **not** a full nonlinear editor.

### Representative brief
“Create a five-minute documentary about the product's origins from these interviews and B-roll. Remove repetition, prioritize the founder's explanation, use reaction shots where helpful, preserve the approved introduction, and make a 16:9 and 9:16 version.”

### Non-goals initially
Advanced grading, compositing, generative footage, professional audio mastering, collaborative real-time NLE, full motion graphics, live production.

### System invariants
- Source media immutable, content-addressed where feasible; all edit operations declarative and reversible.
- Observations, hypotheses, external facts, user corrections, editorial preferences and accepted edits stored separately.
- Every inferred item carries source time span, evidence references, model/version/config, confidence or calibrated uncertainty, and provenance.
- Never silently overwrite human-locked decisions; regeneration creates proposals or explicit conflicts.
- Model implementations interchangeable through versioned interfaces and benchmark fixtures.
- Source time, project time and render time distinguished, with rational timebases and explicit transforms.
- Every accepted edit is reproducible from a project revision, assets and renderer version.

## 2. Architecture
```
Immutable source media + external evidence
        |
Ingest / probe / sync / proxies / source indexing
        |
Parallel analyzers: ASR | diarization | faces | tracking | active speaker |
                   sound events | visual embeddings | VLM captions | motion
        |
Evidence store + identity graph + time-aligned semantic event graph
        |                    ^
        v                    | corrections, labels, scripts, external media
Candidate segmentation / hierarchical event descriptions
        |
Editorial planner: select / omit / order / pace / B-roll / continuity
        |
Virtual director: angle choice / crop / pan / zoom / reaction shots
        |
Versioned edit graph + validation + transactional review
        |
Preview, render, OTIO interchange, MCP control
```
This is a dependency DAG with bounded iterative refinement, not a single-pass conveyor belt. Selective high-resolution analysis can be requested when the planner lacks evidence.

## 3. Data model
**MediaAsset:** ID, cryptographic hash, URI, streams, codec metadata, rotation, dimensions, frame rate, duration, timecode, rights, synchronization transforms.

**EvidenceArtifact:** ID, kind, source IDs, temporal/spatial support, payload or blob reference, producer, model/config/version, input artifact hashes, created time, confidence, quality flags. Artifacts are append-only; supersession is explicit.

**Entity:** stable project-local person/voice/face/object/location/topic ID. Associations are typed probabilistic edges: face-track ↔ person, voice-cluster ↔ person, voice ↔ active visible face. Do not confuse diarization IDs with real identities. User-supplied names are asserted facts with provenance, not inferred from appearance.

**SemanticEvent:** start/end, hierarchy level, action/topic, participants, location, natural-language descriptions, structured attributes, evidence links, proposed boundary alternatives, uncertainty. Multiple overlapping events allowed; do not force a single partition of reality.

**EditGraph:** immutable revision of ordered video/audio tracks, source-range references, source-to-output time maps, nested compositions, transitions, transforms, audio gain, keyframed virtual-camera trajectory, captions, and editorial rationale. A clip occurrence has a separate ID from its source media.

**Constraint/Override:** target artifact or edit, scope, priority, provenance, validity range, lock mode (advisory/prefer/require/forbid), reason. Detect unsatisfiable constraints and surface conflicts.

**Proposal:** base revision, edit patch, predicted impacts, confidence, evidence, validation results, preview ID, author/agent, acceptance state. A proposal never mutates a revision until committed.

**Job:** dependency keys, resource budget, state, progress, cancellation, logs, results and retry policy.

### Time correctness
Store frame timestamps as rational values or integer ticks with a declared timebase, not floating-point seconds. Preserve variable-frame-rate presentation timestamps, audio sample clocks, sync offsets, drop-frame timecode semantics, and speed-ramp mapping. Distinguish inclusive/exclusive boundaries. Test source→timeline→source round-trips, nested compositions, overlaps and audio drift.

### Storage recommendation
Start with PostgreSQL (relational entities, revisions, edges, jobs, metadata), object storage/local filesystem for assets and heavy artifacts, and a vector index for semantic search. Use Parquet or columnar files for dense framewise signals. Avoid committing to a graph database or distributed workflow system until scale demands it.

### OTIO
Use OpenTimelineIO as the initial interchange format, not the canonical semantic store. Benchmark round-trip fidelity for clips, tracks, time ranges, markers, effects, speed changes, nested timelines and metadata. Build explicit capability-loss reporting for unsupported features. https://github.com/AcademySoftwareFoundation/OpenTimelineIO

## 4. Pipeline stages and research programme
Each stage has: contract, candidates, baselines, benchmark, failure analysis, cost measurement, selection gate. Keep original model scores and adjudications for re-evaluation.

### S0. Ingest and synchronization [P0]
Inputs: video, audio, stills, scripts, subtitles, shot logs, camera metadata, externally supplied identity/scene labels. Outputs: immutable assets, indexed streams, aligned clock transforms, proxies and thumbnails.
Candidates: FFprobe/FFmpeg; PyAV; media fingerprinting; waveform cross-correlation; audio/video timecode sync; optional camera-clock correction.
Evaluate: sync offset absolute error (ms), accumulated drift, variable frame-rate correctness, corrupt media recovery, format coverage, proxy fidelity, ingest wall time/GB. Gate: zero silent timebase corruption; round-trip deterministic frame selection.

### S1. Speech and audio understanding [P0]
Subtasks: voice activity detection, ASR with word-level timing, punctuation, diarization including overlap, acoustic event detection, music/speech segmentation, voice identity embeddings, optional source separation.
Candidates: Whisper-family and alternative ASR; forced alignment (WhisperX-style); pyannote.audio vs NeMo diarization; embedding-based speaker clustering; audio event classifiers; optional commercial APIs.
Evaluate: WER/CER; word timestamp median/p95 absolute error; diarization DER/JER (with and without overlap); speaker counting, missed speech, cross-session speaker linkage, cost per source-hour, GPU memory, latency. Include accented speech, crosstalk, background music and off-screen voices.
Research: https://github.com/pyannote/pyannote-audio ; https://arxiv.org/abs/2509.26177

### S2. Faces, people and object identity [P0/P1]
Subtasks: face/person/object detection, temporal tracking, face embeddings for recurring identity, person re-identification, pose, saliency, visibility/occlusion.
Candidates: RetinaFace/YOLO-family face detectors; ByteTrack/BoT-SORT; SAM 2 for object masks; ArcFace/InsightFace for face embedding; pose models. Explicitly evaluate privacy, model license and demographic robustness; face recognition is opt-in and may require consent/retention controls.
Evaluate: detection AP, HOTA/IDF1, ID switches per hour, track fragmentation, identity precision at fixed false-match rate, occlusion recovery, spatial stability and inference cost. False identity merges are more damaging than unlinked identities.

### S3. Audiovisual speaker association [P1]
Link diarized voice segments to visible face tracks using mouth motion, audio-visual synchronization and temporal context. Support off-camera speakers, dubbing, voiceover and multiple visible listeners.
Candidates: TalkNet/TS-TalkNet and modern active-speaker models; voice-face identity graph; simple lip-motion + diarization baseline.
Evaluate: AVA-ActiveSpeaker mAP, face-to-speaker association precision/recall, false association rate, performance under cuts, overlap, silent reaction shots and off-screen speech. Never force a face match.

### S4. Visual/audio semantic understanding [P0]
Generate multiple complementary representations: shot boundaries, frame embeddings, object/action tracks, audio events, timestamped factual descriptions, longer narrative summaries and structured event assertions. Captions must distinguish directly observed facts from inferences.
Candidates: frame-sampled VLMs; video-native VLMs; caption-first hierarchical summarization; contrastive visual embeddings; transcript-only baseline; multimodal fusion; dense video captioning and temporal grounding.
Evaluate: event/action recall, factual claim precision, hallucination rate, temporal localization IoU, entity consistency, omission rate, cost per hour, robustness to sparse frame sampling. Human annotation must identify evidence-supported claims rather than reward verbose plausible descriptions.
Research: VidChapters-7M https://arxiv.org/abs/2309.13952 ; ActivityNet Captions; YouCook2; Ego4D.

### S5. Scene/event segmentation [P0]
Output a hierarchy of candidate boundaries (shot / action / topic / event / narrative scene) with a boundary confidence distribution and reason, not just a flat partition. Propose boundaries inside uncut footage.
Candidates: visual/audio change-point detection (PELT, kernel CPD); transcript topic shifts; LLM over timestamped captions/transcript; direct video VLM boundary reasoning; joint caption-boundary iterative refinement; constrained global segmentation via dynamic programming.
Distinguish semantic change time from cuttable frame: a topic can change during speech, but the actual cut may be a few seconds later. Generate candidate ranges, then optimize cuts for speech completion, motion continuity, shot rhythm and editorial brief.
Evaluate: tolerance-based boundary precision/recall/F1 at 0.5/2/5s, segment covering, hierarchy consistency, action completeness, clip standalone coherence, manual correction effort. Compare human-human disagreement and score multiple acceptable references. Public datasets are auxiliary, not final editorial-quality ground truth.

### S6. Editorial selection and assembly [P0: core differentiator]
Inputs: semantic graph, brief, required/excluded facts, target duration/aspect ratio, rights, locked edits, media. Outputs: evidence-grounded edit proposals with alternate choices.
Tasks: identify best takes, remove filler/dead air/repetitions, preserve coherent sentences, select useful quotes, detect contradictions, order narrative beats, find B-roll, maintain context, enforce continuity and time budget. Treat the target duration as an optimization constraint, not an invitation to speed up speech indiscriminately.
Candidates: transcript-first heuristic baseline; retrieval + LLM outline + constrained segment selection; mixed-integer/dynamic programming selection; hierarchical agent planning with local critique; multimodal end-to-end planner. Test planning and optimization separately.
Evaluate: required-information coverage, unsupported claim count, redundancy, coherence, continuity violations, duration error, provenance completeness, blind pairwise human preference, editing minutes to acceptable result, and diversity of useful alternative cuts. Assess against editor-produced references, but avoid exact-cut imitation as the sole metric.

### S7. Virtual directing and reframing [P1]
Inputs: chosen source intervals, tracked subjects, active speaker, actions, output aspect ratio and style constraints. Outputs: time-indexed crop/keyframes, shot selection, virtual pans/zooms and cut recommendations.
Candidates: active-speaker centering baseline; rule-based cinematographic composition; saliency/action-aware cropping; learned shot quality ranking; constrained trajectory optimization; VLM editorial intent + deterministic trajectory optimizer.
Objective includes subject visibility, headroom/lookroom, action coverage, compositional balance, source resolution, camera velocity/acceleration/jerk, zoom oscillation and cut frequency. Support anticipation using future frames, intentional static shots, and graceful fallback to wide framing.
Evaluate: subject visibility, crop resolution, head/limb clipping, trajectory smoothness, unnecessary moves per minute, speaker-change reaction latency, shot variety, preference of blinded human editors. Compare 16:9→9:16 and multicamera switching separately.

### S8. Preview/render/export [P0]
Render edits without changing source media. Start with FFmpeg-based deterministic offline rendering and proxy preview; consider MLT/GStreamer/GPU graph only when profiling shows a need. Store render manifests, exact edit revision, filter/encoder versions and output checksums. Support partial-region preview and cached intermediate clips.
Evaluate: preview startup time, scrubbing latency, render throughput, audio sync, frame-accurate cuts, filter equivalence between preview/final, output determinism under pinned environments, quality of transitions and failure recovery.

## 5. Editorial optimizer
A proposed edit E maximizes a weighted quality function subject to hard constraints:

`score(E) = coverage + coherence + clip_quality + visual_quality + pacing - redundancy - discontinuity - motion_cost - duration_penalty`

Hard constraints include required/forbidden content, media rights, locked edits, valid source ranges, output format and timing. Soft weights derive from editorial style profiles and brief. Rather than expose an opaque scalar alone, retain component scores and human-readable reasons. LLMs propose semantic choices; deterministic validators check ranges and constraints; optimizers refine selection and virtual-camera paths. Generate several Pareto-distinct proposals, not only the top score.

## 6. Incremental recomputation and manual interventions
Artifact keys = hash(input artifact IDs + producer version + parameters + relevant overrides). Track dependency edges at interval/identity/clip granularity when possible. A corrected speaker name should update identity-linked captions and labels, not re-run ASR. A corrected diarization interval may require local audiovisual association, scene understanding and editing replans, but not unrelated media.

Correction lifecycle: `assert/override → validate → compute impacted dependencies → show proposed invalidations → recompute in branch → compare → commit`. Locked edits remain intact; conflicts become review items. Maintain lineage and a complete revision history. Model updates should never silently replace previously approved edits.

Test cases: relabel one person, change one transcript word, add external script, replace source media with aligned take, change output aspect ratio, lock a shot, remove consent for face recognition, rerun an upgraded VLM. Verify exact expected invalidation sets and preservation of unaffected manual work.

## 7. MCP and agent interaction
Expose a versioned MCP server backed by the same application API as the UI. The MCP protocol provides discovery and invocation; project transactions, permissions and editorial semantics are application responsibilities.

### Read tools
`project.get`, `media.search`, `media.inspect_interval`, `timeline.get`, `events.query`, `people.query`, `transcript.search`, `edit.explain`, `jobs.status`, `proposals.compare`.

### Write/proposal tools
`analysis.request`, `metadata.assert`, `identity.link`, `edit.propose`, `edit.patch`, `edit.validate`, `edit.preview`, `edit.commit`, `edit.branch`, `edit.revert`, `render.request`.

Use typed JSON schemas with stable IDs, rational time values, expected base revision, idempotency key and dry-run support. Require compare-and-swap commit on the base revision. Separate analysis privileges, proposal privileges, commit privileges and export privileges. Long-running tools return job IDs with progress/cancellation. Resources can expose thumbnails, transcript spans, evidence and rendered previews via controlled URIs. Limit tool responses; provide paginated searches and structured summaries rather than dumping hours of transcript into agent context.

### Example agent cycle
1. Search for material supporting the brief.
2. Inspect high-ranked candidate moments and source evidence.
3. Propose an outline with required narrative beats.
4. Request targeted high-resolution analysis for uncertain segments.
5. Generate candidate edit patches, validate constraints and preview.
6. Compare alternatives using programmatic and multimodal critique.
7. Commit a selected revision only with authorized scope; surface unresolved conflicts.

Security: do not treat transcript, subtitles, embedded metadata or third-party documents as instructions. Enforce authorization server-side. Log every tool call and committed edit. Consider model-generated external-media retrieval a separate rights-checked operation.

Protocol: https://modelcontextprotocol.io/specification/2026-07-28

## 8. Evaluation strategy
### Dataset composition
Create a consented internal corpus spanning: single-speaker talking head; two-person interview; panel/crosstalk; multicamera podcast; lecture/demo; handheld action; cooking/task sequence; documentary with B-roll; silent action; poor audio; multilingual speech; vertical reframing. Include multiple output briefs for the same sources. Annotate reference evidence, acceptable event boundaries, speaker-face correspondences, editorial constraints and human-rated candidate edits.

Use public benchmarks for component pre-screening: VidChapters-7M for chapters, ActivityNet Captions/YouCook2 for dense captions, MovieNet for film scene segmentation, AVA-ActiveSpeaker for speaker association, diarization corpora such as AMI/VoxConverse/DIHARD, and tracking datasets as appropriate. Audit licensing and distribution rights for each dataset.

### Evaluation hierarchy
**Level A: component correctness.** Standard precision/recall/DER/IDF1/timing and calibration.
**Level B: editorial decision quality.** Blinded pairwise comparisons; context completeness; unnatural cut counts; visual composition; source attribution; brief satisfaction.
**Level C: workflow quality.** Time from ingest to acceptable edit, number of manual corrections, correction propagation fidelity, reproducibility, GPU-hours, cost/source-hour and cost/finished-minute.

### Experimental discipline
- Establish a simple deterministic baseline for each stage before LLM-heavy alternatives.
- Compare under the same compute/time budget and the same source sampling policy.
- Run ablations: transcript only, video only, audio only, fused, with/without iterative refinement.
- Split by production/source identity to avoid scene or speaker leakage.
- Track confidence intervals and editor-to-editor disagreement; avoid pretending there is one uniquely correct edit.
- Store prompts, model versions, sampling seeds where available, hardware, latency and price.
- Evaluate propagation of upstream errors through final edits, not merely isolated component scores.

### Provisional acceptance gates (targets, not research findings)
- Ingest: no silent timing corruption on a diverse format suite; frame-accurate source references.
- Corrections: 100% preservation of locked decisions in automated runs, or explicit blocking conflict.
- Agent: zero unauthorized committed mutations; deterministic conflict detection on stale revisions.
- Semantic: improvement over transcript-only baseline on blinded editorial utility, with measured cost.
- Framing: fewer major subject crop errors than static center-crop baseline, without increased distracting motion.
- Product: meaningful reduction in median human minutes to a publishable rough cut against a manual baseline. Quantify only after pilot study.

## 9. Implementation and experimental roadmap
**M0 — Foundations (2–4 weeks):** immutable ingest, frame-accurate time model, asset/proxy store, versioned edit graph, simple preview, OTIO export. Tests for timing and reproducibility.

**M1 — Semantic indexing (3–5 weeks):** ASR, diarization, shot detection, frame embeddings, transcript/visual search, face tracks, evidence schema and review interface. Benchmark cost and accuracy.

**M2 — Segmentation lab (3–5 weeks):** transcript-only, change-point, VLM-caption and hybrid algorithms; hierarchy, boundary refinements, editor-labelled dataset and side-by-side evals.

**M3 — Automated rough cuts (4–7 weeks):** brief-to-outline, clip retrieval/selection, narrative ordering, redundancy removal, global constraints, alternative edit proposals and pairwise review.

**M4 — Automated directing (3–6 weeks):** active-speaker association, subject tracking, virtual crop optimizer, 9:16 reframing, optional multicamera selection and framing evals.

**M5 — Agent and feedback loop (3–5 weeks):** MCP API, proposals/commits, selective reanalysis, override invalidation, explainability, revision comparison, security tests.

These are rough single-team engineering estimates with research uncertainty, not delivery commitments. Parallel work is possible after M0. A useful first end-to-end demo should be possible before M4.

## 10. Open architectural decisions and decision experiments
1. **Canonical edit model:** custom immutable graph vs OTIO-extended representation. Decide by provenance/invalidation support, edit expressivity, round-trip loss and implementation complexity.
2. **Analysis granularity:** dense frame processing vs adaptive sampling. Decide by event recall per GPU-hour and latency.
3. **Captioning:** transcript+frame caption-first vs video-native event segmentation vs iterative. Decide by grounded event recall and downstream editing benefit.
4. **Identity model:** per-source clustering vs cross-source persistent identities. Decide by false merges, consent constraints and correction costs.
5. **Planner:** single LLM proposal vs hierarchical planner+optimizer vs multi-agent critique. Decide by blind human preference per dollar and correction effort.
6. **Reframing:** hand-designed composition optimizer vs learned crop policy. Decide by human preference, error frequency and motion smoothness.
7. **Runtime:** local-first vs remote GPU jobs vs hybrid. Decide after profiling throughput, privacy and model licensing.
8. **Preview engine:** FFmpeg pipeline vs dedicated media graph. Decide by latency, fidelity and support for keyframed effects.

## 11. Risk register
- **Semantic hallucination:** models invent actions, relationships or motives. Mitigate with evidence-linked assertions, grounded scoring and human review for consequential edits.
- **Error propagation:** wrong diarization contaminates descriptions, framing and selection. Mitigate with uncertainty, alternate hypotheses and targeted reanalysis.
- **Temporal precision:** LLM timestamps may be inaccurate. Separate coarse reasoning from frame/sample-accurate alignment.
- **Editorial subjectivity:** a single ground-truth edit is misleading. Use multiple acceptable references and blind editor preference studies.
- **Privacy and rights:** face embeddings, speaker identification, transcripts and third-party footage may be sensitive. Require project-level retention, access control, deletion, consent and rights metadata.
- **Non-determinism:** changing models/prompts can destabilize edits. Pin versions, preserve outputs, branch proposals and protect approved revisions.
- **Compute explosion:** expensive VLM analysis on all frames is uneconomic. Use hierarchical sampling, cached embeddings, selective reanalysis and explicit budgets.
- **Over-engineering:** do not build a full NLE, graph database or multi-agent platform before demonstrating that automated clip selection produces good edits.

## 12. Research bibliography and implementation references
- OpenTimelineIO: https://github.com/AcademySoftwareFoundation/OpenTimelineIO
- VidChapters-7M: https://arxiv.org/abs/2309.13952
- pyannote.audio: https://github.com/pyannote/pyannote-audio
- Benchmarking Diarization Models (2025): https://arxiv.org/abs/2509.26177
- AVA-ActiveSpeaker benchmark: https://research.google.com/ava/
- SAM 2: https://github.com/facebookresearch/sam2
- ByteTrack: https://arxiv.org/abs/2110.06864
- ruptures change-point detection: https://centre-borelli.github.io/ruptures-docs/
- Model Context Protocol specification: https://modelcontextprotocol.io/specification/2026-07-28
- FFmpeg: https://ffmpeg.org/

**Research status note:** This is an engineering design and experiment plan, not a claim that a particular model wins on editorial quality. Model benchmarks are often incomparable across datasets, evaluation settings and hardware. The first research milestone must reproduce baselines on our own footage before selecting a production stack.
