// The mock server: MSW handlers for every endpoint the app calls, answering like a healthy server.
// One set serves the jsdom tests (Node, tests/utils/server.ts), Storybook (a service worker,
// .storybook/preview.tsx) and the visual suite (Node, strata-visual.config.ts), so the app's real
// fetch, query and DataView code runs in all three and nothing is a module mock.
//
// The named handlers below each replace one route to put a test, story or screenshot into another
// state: `server.use(sourcesError)` in a test, `msw.use(sourcesError)` in a story's beforeEach,
// `states: { error: [sourcesError] }` in the visual config.
import { delay, HttpResponse, http } from 'msw'
import type { Chapter, People, ReelDetail, Render, Revision, Scenes, Source, WorkflowRun } from '../api'
import { initialSources, systemModelsDown as modelsDown, systemNearlyFull as nearlyFull, readSource, systemStatus } from './fixtures'
import { clipsOf, initialReels, initialRenders, listed, renderOf, revisionOf } from './reels'
import {
  faceSvg,
  fresh,
  groupOf,
  jobsPage,
  knownPeople,
  otherRuns,
  peopleWaiting,
  proxyWebm,
  tapeId,
  tapePeaks,
  tapePeople,
  tapePeopleNaming,
  tapeRun,
  tapeRunChecked,
  tapeRunDone,
  tapeRunFailed,
  tapeRunNaming,
  tapeRunPartWay,
  tapeScenes,
  tapeScenesPartWay,
  tapeTranscript,
  thumbnailSvg,
  waitingScenes,
} from './tape'

/** What the mock server holds. Writes change it, so a test can add a source and see it listed. */
export const mock = {
  sources: [...initialSources] as Source[],
  jobs: structuredClone(jobsPage.items),
  /** Each tape's run, where a test has put one; otherwise the tape (5) is done and the others wait for their scenes. */
  runs: {} as Record<number, WorkflowRun>,
  /** The person's chapter marks on the tape, by scene index, and titles, by the index of the chapter's first scene. */
  marks: {} as Record<number, boolean>,
  titles: {} as Record<number, string>,
  reels: structuredClone(initialReels) as ReelDetail[],
  renders: structuredClone(initialRenders) as Render[],
  /** Each tape's faces, where a test has put them (or answered a group); otherwise the tape (5) is all named. */
  people: {} as Record<number, People>,
  known: structuredClone(knownPeople),
}

/** Puts the tape where its people wait to be named: three groups to answer, the describe step waiting for them. */
export function startNaming() {
  mock.people[tapeId] = structuredClone(tapePeopleNaming)
  mock.runs[tapeId] = tapeRunNaming
}

/** Back to the healthy state: call between tests and stories. */
export function resetMock() {
  mock.sources = [...initialSources]
  mock.jobs = structuredClone(jobsPage.items)
  mock.runs = {}
  mock.marks = {}
  mock.titles = {}
  mock.reels = structuredClone(initialReels)
  mock.renders = structuredClone(initialRenders)
  mock.people = {}
  mock.known = structuredClone(knownPeople)
}

/** A reel's numbers again after a change, as `reels.detail` counts them. */
function recount(r: ReelDetail): ReelDetail {
  r.current_id = r.current?.id ?? null
  r.clips = r.current?.clips.length ?? null
  r.duration_seconds = r.current?.duration_seconds ?? null
  r.proposals = r.open.filter(v => v.state === 'proposal').length
  return r
}
const nextRevision = () => Math.max(0, ...mock.reels.flatMap(r => [r.current?.id ?? 0, ...r.open.map(v => v.id)])) + 1

/** The tape's scenes with the person's marks and titles, grouped again at once (the server's job does it a moment
 *  later): a chapter starts where the grouping or a mark says so; one with the scenes of a named chapter keeps its name. */
export function correctedScenes(marks = mock.marks, titles = mock.titles): Scenes {
  const starts = new Set((tapeScenes.chapters ?? []).map(c => c.scenes[0]))
  for (const [scene, start] of Object.entries(marks)) {
    if (start) starts.add(Number(scene))
    else starts.delete(Number(scene))
  }
  starts.add(0)
  const runs: number[][] = []
  for (const s of tapeScenes.scenes) {
    if (starts.has(s.index)) runs.push([s.index])
    else runs[runs.length - 1].push(s.index)
  }
  const chapters = runs.map((scenes, index): Chapter => {
    const was = tapeScenes.chapters?.find(c => c.scenes.join() === scenes.join())
    const mark = marks[scenes[0]]
    const first = tapeScenes.scenes[scenes[0]]
    const last = tapeScenes.scenes[scenes[scenes.length - 1]]
    return {
      index,
      start: first.start,
      end: last.end,
      scenes,
      confidence: index === 0 ? null : mark === undefined ? (was?.confidence ?? 0.5) : 1,
      reasons: mark === undefined ? (was?.reasons ?? []) : [...(was?.reasons ?? []), 'you started a chapter here'],
      name: was?.name ?? null,
      title: titles[scenes[0]] ?? null,
    }
  })
  return {
    ...tapeScenes,
    analysis: { ...tapeScenes.analysis, named: chapters.filter(c => c.name).length },
    scenes: tapeScenes.scenes.map(s => ({ ...s, mark: marks[s.index] ?? null })),
    chapters,
  }
}

/** A source's run as the server would answer it now. */
const runOf = (id: number): WorkflowRun =>
  mock.runs[id] ??
  otherRuns[id] ??
  (id === tapeId
    ? tapeRunDone
    : tapeRun(id, {
        scenes: 'ready',
        faces: 'ready',
        face_tracks: 'ready',
        store_faces: 'blocked',
        name_people: 'blocked',
        cast: 'blocked',
        transcript: 'ready',
        describe: 'blocked',
        chapters: 'blocked',
        name_chapters: 'blocked',
      }))

// Matched as paths, so they answer whatever origin and base the app runs under.
const at = (path: string) => `*${path}`

/** The endpoints, as the server's paths (src/app/server/app.py; the same keys as api.generated.ts). */
export const paths = {
  health: '/api/health',
  system: '/api/system',
  sources: '/api/sources',
  source: '/api/sources/:id',
  scenes: '/api/sources/:id/scenes',
  transcript: '/api/sources/:id/transcript',
  peaks: '/api/sources/:id/peaks',
  proxy: '/api/sources/:id/proxy',
  thumbnail: '/api/sources/:id/thumbnails/:name',
  jobs: '/api/jobs',
  jobCounts: '/api/jobs/counts',
  cancel: '/api/jobs/:id/cancel',
  retry: '/api/jobs/:id/retry',
  chapterMark: '/api/sources/:id/scenes/:scene/chapter-mark',
  chapterTitle: '/api/sources/:id/scenes/:scene/chapter-title',
  people: '/api/sources/:id/people',
  faceCrop: '/api/sources/:id/faces/:face/crop',
  known: '/api/people',
  runs: '/api/workflows/tape/subjects',
  run: '/api/workflows/tape/subjects/:subject',
  runAgain: '/api/workflows/tape/subjects/:subject/run',
  approve: '/api/workflows/tape/subjects/:subject/stages/:stage/approve',
  reels: '/api/reels',
  reel: '/api/reels/:id',
  plan: '/api/reels/:id/plan',
  accept: '/api/reels/:id/revisions/:rev/accept',
  reject: '/api/reels/:id/revisions/:rev/reject',
  render: '/api/reels/:id/revisions/:rev/render',
  renders: '/api/reels/:id/renders',
  renderFile: '/api/reels/:id/renders/:render/file',
} as const

const refused = (detail: string, status = 400) => HttpResponse.json({ detail }, { status })
const notFound = (detail: string) => HttpResponse.json({ detail }, { status: 404 })
const sourceOf = (id: unknown) => mock.sources.find(s => s.id === Number(id))
const down = () => new HttpResponse(null, { status: 503, statusText: 'Service Unavailable' })
/** A subject's source id, as `jobs.source_id_of` reads it (`source:5`, escaped or not). */
const reelOf = (id: unknown) => mock.reels.find(r => r.id === Number(id))
const noReel = (id: unknown) => notFound(`there is no reel ${String(id)}`)
/** A version among the reel's current and open ones. */
const versionOf = (r: ReelDetail, rev: unknown): Revision | undefined =>
  [r.current, ...r.open].find(v => v?.id === Number(rev)) ?? undefined
/** A source's faces as the server would answer them now. */
const peopleOf = (id: number): People => mock.people[id] ?? (id === tapeId ? structuredClone(tapePeople) : peopleWaiting())
const subjectId = (subject: unknown) => Number(/^source:(\d+)$/.exec(decodeURIComponent(String(subject)))?.[1] ?? Number.NaN)

export const handlers = [
  http.get(at(paths.health), () => HttpResponse.json({ ok: true, version: systemStatus.version, commit: systemStatus.commit })),
  http.get(at(paths.system), () => HttpResponse.json({ ...systemStatus, sources: mock.sources.length })),
  http.get(at(paths.sources), () => HttpResponse.json(mock.sources)),
  // As sources.add does: a path under /missing/ is a file that is not there; a path already held is
  // the same record again with 200; anything else is read and kept with 201.
  http.post(at(paths.sources), async ({ request }) => {
    const { path } = (await request.json()) as { path: string }
    if (path.startsWith('/missing/')) return refused(`there is no file ${path}`)
    const known = mock.sources.find(s => s.path === path)
    if (known) return HttpResponse.json(known)
    const entry = readSource(Math.max(0, ...mock.sources.map(s => s.id)) + 1, path)
    mock.sources = [entry, ...mock.sources]
    return HttpResponse.json(entry, { status: 201 })
  }),
  http.get(at(paths.source), ({ params }) => {
    const found = sourceOf(params.id)
    return found ? HttpResponse.json(found) : notFound(`there is no source ${String(params.id)}`)
  }),
  // The tape (source 5) has every result; the others are still waiting for their scenes.
  http.get(at(paths.scenes), ({ params }) => {
    const found = sourceOf(params.id)
    if (!found) return notFound(`there is no source ${String(params.id)}`)
    return HttpResponse.json(found.id === tapeId ? correctedScenes() : waitingScenes(found.id))
  }),
  // As chapters.mark and chapters.rename do: refused where the server refuses, else answered with the scenes after.
  http.put(at(paths.chapterMark), async ({ params, request }) => {
    if (Number(params.id) !== tapeId) return notFound(`${String(params.id)} has no scenes yet`)
    const scene = Number(params.scene)
    const n = tapeScenes.scenes.length
    if (!(scene > 0 && scene < n)) return refused(`a chapter can be marked at scenes 2 to ${n}, not scene ${scene + 1}`)
    const { start } = (await request.json()) as { start: boolean | null }
    if (start === null) delete mock.marks[scene]
    else mock.marks[scene] = start
    // a changed check: the chapters regrouped, so the person is asked again
    mock.runs[tapeId] = tapeRunDone
    return HttpResponse.json(correctedScenes())
  }),
  http.put(at(paths.chapterTitle), async ({ params, request }) => {
    if (Number(params.id) !== tapeId) return notFound(`${String(params.id)} has no chapters yet`)
    const chapter = correctedScenes().chapters?.find(c => c.scenes[0] === Number(params.scene))
    if (!chapter) return refused(`no chapter starts at scene ${Number(params.scene) + 1} now: read the chapters again`)
    const { title } = (await request.json()) as { title: string | null }
    if (title === null) delete mock.titles[chapter.scenes[0]]
    else if (!title.trim()) return refused('a chapter’s title needs a word or more')
    else mock.titles[chapter.scenes[0]] = title.trim()
    return HttpResponse.json(correctedScenes())
  }),
  // src/app/people.py as the routes answer it: an answer takes its group off the list and counts its tracks; once
  // nothing is left to answer, the gate approves itself and the tape's job goes on.
  http.get(at(paths.people), ({ params }) => {
    const found = sourceOf(params.id)
    return found ? HttpResponse.json(peopleOf(found.id)) : notFound(`there is no source ${String(params.id)}`)
  }),
  http.post(at(paths.people), async ({ params, request }) => {
    const found = sourceOf(params.id)
    if (!found) return notFound(`there is no source ${String(params.id)}`)
    const b = (await request.json()) as {
      tracks: string[]
      answer: 'name' | 'not-faces' | 'skip'
      name?: string | null
      person?: number | null
    }
    const p = peopleOf(found.id)
    // The answer may be for part of a group (the person split off the faces that are someone else): those tracks go,
    // and the rest stay a group, as the server regroups what is left.
    const whole = p.groups.find(g => b.tracks.length > 0 && b.tracks.every(t => g.tracks.includes(t)))
    if (!whole) return refused(`${b.tracks.join(', ')} are not tracks of this tape left to answer`)
    const chosen = whole.members.filter(m => b.tracks.includes(m.track))
    const group = { tracks: b.tracks, members: chosen, seconds: Math.round(chosen.reduce((n, m) => n + m.seconds, 0) * 10) / 10 }
    if (b.answer === 'name') {
      if (!b.name?.trim() && b.person == null) return refused('a name, or a person known already, is needed to name them')
      let who = b.person == null ? undefined : mock.known.find(k => k.id === b.person)
      if (b.person != null && !who) return notFound(`there is no person ${b.person}`)
      if (!who) {
        who = { id: Math.max(0, ...mock.known.map(k => k.id)) + 1, name: b.name!.trim(), faces: 0 }
        mock.known.push(who)
      }
      who.faces += group.members.length
      const had = p.people.find(x => x.id === who.id)
      if (had) Object.assign(had, { tracks: had.tracks + group.tracks.length, seconds: had.seconds + group.seconds })
      else
        p.people.push({
          id: who.id,
          name: who.name,
          tracks: group.tracks.length,
          seconds: group.seconds,
          face: group.members[0]?.face ?? null,
        })
      p.people.sort((x, y) => y.seconds - x.seconds)
      p.named += group.tracks.length
    } else if (b.answer === 'skip') p.skipped += group.tracks.length
    else p.not_faces += group.tracks.length
    const rest = whole.members.filter(m => !b.tracks.includes(m.track))
    p.groups = p.groups.flatMap(g =>
      g !== whole ? [g] : rest.length ? [{ ...groupOf(rest.map(m => [m.track, m.face, m.seconds, m.start])), suggested: g.suggested }] : [],
    )
    p.open = Math.max(0, p.open - group.tracks.length)
    if (!p.groups.length) {
      p.done = true
      if (mock.runs[found.id] === tapeRunNaming) mock.runs[found.id] = tapeRunDone
    }
    mock.people[found.id] = p
    return HttpResponse.json(p)
  }),
  http.get(
    at(paths.faceCrop),
    ({ params }) => new HttpResponse(faceSvg(Number(params.face)), { headers: { 'Content-Type': 'image/svg+xml' } }),
  ),
  http.get(at(paths.known), ({ request }) => {
    const q = new URL(request.url).searchParams.get('q')?.toLowerCase()
    return HttpResponse.json(mock.known.filter(k => !q || k.name.toLowerCase().includes(q)))
  }),
  http.get(at(paths.transcript), ({ params }) =>
    Number(params.id) === tapeId
      ? HttpResponse.json(tapeTranscript)
      : HttpResponse.json({ language: null, model: null, waiting: 'Listening for speech (the transcript step).', segments: [] }),
  ),
  http.get(at(paths.peaks), ({ params }) =>
    Number(params.id) === tapeId ? HttpResponse.json(tapePeaks) : notFound('the waveform is not made yet'),
  ),
  http.get(at(paths.proxy), () => new HttpResponse(proxyWebm, { headers: { 'Content-Type': 'video/webm' } })),
  http.get(at(paths.thumbnail), ({ params }) => {
    const svg = thumbnailSvg(String(params.name))
    return svg ? new HttpResponse(svg, { headers: { 'Content-Type': 'image/svg+xml' } }) : notFound(`no thumbnail ${String(params.name)}`)
  }),
  http.get(at(paths.jobCounts), () =>
    HttpResponse.json(Object.fromEntries(Object.entries(Object.groupBy(mock.jobs, j => j.state)).map(([k, v]) => [k, v?.length ?? 0]))),
  ),
  http.get(at(paths.jobs), ({ request }) => {
    const query = new URL(request.url).searchParams
    const states = query.getAll('state')
    const domain = query.get('domain')
    const items = mock.jobs.filter(j => (!states.length || states.includes(j.state)) && (domain === null || j.domain === domain))
    return HttpResponse.json(fresh({ items, next: null, total: items.length }))
  }),
  // As strata-python's queue does: a queued job is cancelled at once, a running one asked to stop; a
  // failed or cancelled one is queued again; anything else is refused with the server's sentence.
  http.post(at(paths.cancel), ({ params }) => {
    const j = mock.jobs.find(x => x.id === Number(params.id))
    if (!j) return notFound(`there is no job ${String(params.id)}`)
    if (j.state === 'queued' || j.state === 'running') j.state = 'cancelled'
    return HttpResponse.json(j)
  }),
  http.post(at(paths.retry), ({ params }) => {
    const j = mock.jobs.find(x => x.id === Number(params.id))
    if (!j) return notFound(`there is no job ${String(params.id)}`)
    if (j.state !== 'failed' && j.state !== 'cancelled')
      return refused(`job ${j.id} is ${j.state}: only a failed or cancelled job can be run again`)
    Object.assign(j, { state: 'queued', error: null, started_at: null, finished_at: null })
    return HttpResponse.json(j)
  }),
  // strata-python's add_workflow: a tape's stages, and running it again (its failed stages are tried
  // again by a queued job, as `enqueue` dedupes on the tape).
  http.get(at(paths.runs), () => HttpResponse.json({ items: mock.sources.map(s => fresh(runOf(s.id))), next: null })),
  http.get(at(paths.run), ({ params }) => {
    const id = subjectId(params.subject)
    return sourceOf(id) ? HttpResponse.json(fresh(runOf(id))) : notFound(`there is no source ${String(id)}`)
  }),
  http.post(at(paths.runAgain), ({ params }) => {
    const id = subjectId(params.subject)
    if (!sourceOf(id)) return notFound(`there is no source ${String(id)}`)
    const now = runOf(id)
    const job = { ...(now.job ?? jobsPage.items[0]), state: 'queued' as const, error: null, progress: null, finished_at: null }
    mock.runs[id] = {
      ...now,
      state: 'blocked',
      failed: [],
      stages: now.stages.map(s => (s.state === 'failed' ? { ...s, state: 'ready' as const } : s)),
      job,
    }
    return HttpResponse.json(fresh(mock.runs[id]))
  }),
  http.post(at(paths.approve), ({ params }) => {
    const id = subjectId(params.subject)
    if (!sourceOf(id)) return notFound(`there is no source ${String(id)}`)
    if (params.stage === 'name_people') {
      mock.people[id] = { ...peopleOf(id), done: true }
      mock.runs[id] = tapeRunDone
      return HttpResponse.json(fresh(mock.runs[id]))
    }
    if (params.stage !== 'check_chapters') return refused(`${String(params.stage)} is a stage, not a gate: it is run, not approved`)
    mock.runs[id] = tapeRunChecked
    return HttpResponse.json(fresh(mock.runs[id]))
  }),
  // src/app/reels.py and renders.py as the routes answer them. Only the tape (5) has grouped scenes to cut from.
  http.get(at(paths.reels), () => HttpResponse.json(mock.reels.map(listed))),
  http.post(at(paths.reels), async ({ request }) => {
    const { source_id, target_seconds, name } = (await request.json()) as { source_id: number; target_seconds: number; name?: string }
    const source = sourceOf(source_id)
    if (!source) return notFound(`there is no source ${source_id}`)
    if (source.id !== 5) return notFound(`${source.name} has no scenes yet: a reel is cut from them`)
    if (!(target_seconds > 0)) return refused(`a reel's length is above 0 seconds, not ${target_seconds}`)
    const id = Math.max(0, ...mock.reels.map(r => r.id)) + 1
    const rev = revisionOf(nextRevision(), id, 'proposal', clipsOf(Math.max(1, Math.round(target_seconds / 10)), 40, 600, 10))
    const reel = recount({
      id,
      name: name ?? `${source.name}, ${Math.floor(target_seconds / 60)}:${String(target_seconds % 60).padStart(2, '0')}`,
      source_id: source.id,
      source_name: source.name,
      target_seconds,
      created: '2026-11-27T09:00:00Z',
      current_id: null,
      current: null,
      open: [rev],
      proposals: 1,
      clips: null,
      duration_seconds: null,
      constraints: [],
    })
    mock.reels = [...mock.reels, reel]
    return HttpResponse.json(reel, { status: 201 })
  }),
  http.get(at(paths.reel), ({ params }) => {
    const r = reelOf(params.id)
    return r ? HttpResponse.json(r) : noReel(params.id)
  }),
  http.post(at(paths.plan), async ({ params, request }) => {
    const r = reelOf(params.id)
    if (!r) return noReel(params.id)
    const { seed } = (await request.json()) as { seed: number }
    const rev = revisionOf(nextRevision(), r.id, 'proposal', clipsOf(9 + (seed % 4), 70 + seed * 13, 650, 12), seed)
    r.open = [rev, ...r.open]
    recount(r)
    return HttpResponse.json(rev, { status: 201 })
  }),
  // Compare-and-swap, as reels.accept: a stale proposal is a 409; the others made on the old current go stale.
  http.post(at(paths.accept), ({ params }) => {
    const r = reelOf(params.id)
    if (!r) return noReel(params.id)
    const v = r.open.find(x => x.id === Number(params.rev))
    if (!v) return notFound(`reel ${r.id} has no open proposal ${String(params.rev)}`)
    if (v.state === 'stale')
      return refused(`The reel has changed since this proposal was made (its current revision is ${r.current_id}): plan it again.`, 409)
    r.open = r.open.filter(x => x !== v).map(x => ({ ...x, state: 'stale' as const }))
    r.current = { ...v, state: 'current', accepted: '2026-11-27T09:05:00Z', accepted_by: 'person' }
    recount(r)
    return HttpResponse.json(r.current)
  }),
  http.post(at(paths.reject), ({ params }) => {
    const r = reelOf(params.id)
    if (!r) return noReel(params.id)
    const v = r.open.find(x => x.id === Number(params.rev))
    if (!v) return refused(`revision ${String(params.rev)} is not an open proposal: only a proposal can be rejected`)
    r.open = r.open.filter(x => x !== v)
    recount(r)
    return HttpResponse.json({ ...v, state: 'rejected' })
  }),
  // A render is queued; one already made for the version is answered again.
  http.post(at(paths.render), ({ params }) => {
    const r = reelOf(params.id)
    if (!r) return noReel(params.id)
    const v = versionOf(r, params.rev)
    if (!v) return notFound(`reel ${r.id} has no revision ${String(params.rev)}`)
    const made = mock.renders.find(x => x.revision_id === v.id && (x.state === 'done' || x.state === 'queued' || x.state === 'running'))
    if (made) return HttpResponse.json(made, { status: 201 })
    const row = renderOf(Math.max(0, ...mock.renders.map(x => x.id)) + 1, v, 'queued')
    mock.renders = [row, ...mock.renders]
    return HttpResponse.json(row, { status: 201 })
  }),
  http.get(at(paths.renders), ({ params }) => {
    const r = reelOf(params.id)
    return r ? HttpResponse.json(mock.renders.filter(x => x.reel_id === r.id)) : noReel(params.id)
  }),
  http.get(at(paths.renderFile), () => new HttpResponse(proxyWebm, { headers: { 'Content-Type': 'video/mp4' } })),
]

// States. A request that never answers is "loading"; 503 is a server that is down. The app's query
// client retries once, so an error shows after about a second (tests turn retries off).

export const sourcesLoading = http.get(at(paths.sources), () => delay('infinite'))
export const sourcesEmpty = http.get(at(paths.sources), () => HttpResponse.json([]))
export const sourcesError = http.get(at(paths.sources), down)
export const addFails = http.post(at(paths.sources), down)

export const systemLoading = http.get(at(paths.system), () => delay('infinite'))
export const systemError = http.get(at(paths.system), down)
export const systemNearlyFull = http.get(at(paths.system), () => HttpResponse.json(nearlyFull))
export const systemModelsDown = http.get(at(paths.system), () => HttpResponse.json(modelsDown))
/** A server installed from a wheel (no source commit) whose home folder's disk could not be read. */
export const systemUnread = http.get(at(paths.system), () =>
  HttpResponse.json({
    ...systemStatus,
    commit: null,
    folders: [{ name: 'home', path: '/home/ada/.reels', exists: true, free_bytes: null, total_bytes: null }],
  }),
)

export const scenesLoading = http.get(at(paths.scenes), () => delay('infinite'))
export const scenesError = http.get(at(paths.scenes), down)
export const scenesPartWay = http.get(at(paths.scenes), () => HttpResponse.json(tapeScenesPartWay))
/** Grouped into chapters, the names still being written: the second has none yet. */
export const scenesUnnamed = http.get(at(paths.scenes), () =>
  HttpResponse.json({
    ...tapeScenes,
    analysis: { ...tapeScenes.analysis, named: 2 },
    chapters: tapeScenes.chapters!.map(c => (c.index === 1 ? { ...c, name: null } : c)),
  }),
)
/** Corrected by the person, and grouped by it: the start of chapter 2 and scene 2's place in chapter 1 marked as the
 *  grouping had them, and chapter 2 under their own title. */
export const scenesCorrected = http.get(at(paths.scenes), () =>
  HttpResponse.json(correctedScenes({ 1: false, 3: true }, { 3: 'Dinner at Grandma’s' })),
)
/** A mark the job has not grouped by yet: the chapters as they were, and the page saying it is regrouping. */
export const scenesRegrouping = http.get(at(paths.scenes), () =>
  HttpResponse.json({ ...tapeScenes, regrouping: true, scenes: tapeScenes.scenes.map(s => (s.index === 2 ? { ...s, mark: true } : s)) }),
)
/** The tape before its scenes are grouped. */
export const scenesWaiting = http.get(at(paths.scenes), () => HttpResponse.json(waitingScenes(tapeId)))
/** The people waiting to be named: three groups left, and the tape's run waiting at the gate. */
export const peopleNaming = [
  http.get(at(paths.people), () => HttpResponse.json(tapePeopleNaming)),
  http.get(at(paths.run), () => HttpResponse.json(fresh(tapeRunNaming))),
  http.get(at(paths.scenes), () =>
    HttpResponse.json({
      ...tapeScenes,
      analysis: { ...tapeScenes.analysis, cast: false, described: 0, chapters: false, named: 0 },
      chapters: null,
      scenes: tapeScenes.scenes.map(s => ({ ...s, description: null, people: null })),
    }),
  ),
]
export const peopleLoading = http.get(at(paths.people), () => delay('infinite'))
export const transcriptWaiting = http.get(at(paths.transcript), () =>
  HttpResponse.json({ language: null, model: null, waiting: 'Listening for speech (the transcript step).', segments: [] }),
)
export const peaksLoading = http.get(at(paths.peaks), () => delay('infinite'))
// The queue's states answer its list and its counts alike (`/api/jobs*`), so the finding agrees with the table.
const queue = (answer: (counts: boolean) => Response | Promise<Response>) =>
  http.get(at(`${paths.jobs}*`), ({ request }) => answer(new URL(request.url).pathname.endsWith('/counts')))
export const jobsLoading = queue(async () => {
  await delay('infinite')
  return down()
})
export const jobsEmpty = queue(counts => HttpResponse.json(counts ? {} : { items: [], next: null, total: 0 }))
export const jobsError = queue(down)
/** More jobs than the page shows: the list is cut, and says so. */
export const jobsMore = http.get(at(paths.jobs), () => HttpResponse.json(fresh({ items: mock.jobs, next: 'c200', total: 1340 })))

/** The tape part way through its pipeline, its job running. */
export const runPartWay = http.get(at(paths.run), () => HttpResponse.json(fresh(tapeRunPartWay)))
/** The tape's faces stage failed, and its job waits to try again. */
export const runFailed = http.get(at(paths.run), () => HttpResponse.json(fresh(tapeRunFailed)))
/** The person has confirmed the chapters. */
export const runChecked = http.get(at(paths.run), () => HttpResponse.json(fresh(tapeRunChecked)))
export const runError = http.get(at(paths.run), down)
/** The tape before its scenes are grouped: the scenes step next, nothing running (as `scenesWaiting`). */
export const runWaiting = http.get(at(paths.run), () =>
  HttpResponse.json(
    fresh(
      tapeRun(tapeId, {
        scenes: 'ready',
        faces: 'ready',
        transcript: 'ready',
        describe: 'blocked',
        chapters: 'blocked',
        name_chapters: 'blocked',
      }),
    ),
  ),
)

export const reelsLoading = http.get(at(paths.reels), () => delay('infinite'))
export const reelsEmpty = http.get(at(paths.reels), () => HttpResponse.json([]))
export const reelsError = http.get(at(paths.reels), down)
export const reelLoading = http.get(at(paths.reel), () => delay('infinite'))
export const reelError = http.get(at(paths.reel), down)
/** The tape's reel with its current version's render waiting on the queue. */
export const renderQueued = http.get(at(paths.renders), () =>
  HttpResponse.json([renderOf(1, initialReels[0].current as Revision, 'queued')]),
)
/** The render failed: the tape changed on disk since it was added. */
export const renderFailed = http.get(at(paths.renders), () =>
  HttpResponse.json([renderOf(1, initialReels[0].current as Revision, 'failed')]),
)
/** The proposal on the tape's reel made on an older version: it cannot be accepted. */
export const reelStale = http.get(at(paths.reel), () =>
  HttpResponse.json({ ...initialReels[0], proposals: 0, open: initialReels[0].open.map(v => ({ ...v, state: 'stale' })) }),
)
/** Accepting answers 409: someone accepted another version meanwhile. */
export const acceptConflict = http.post(at(paths.accept), () =>
  refused('The reel has changed since this proposal was made (its current revision is 4): plan it again.', 409),
)
/** Rendering is refused: a clip's tape is not hashed yet. */
export const renderRefused = http.post(at(paths.render), () =>
  refused("a clip's tape (source 5) is not hashed yet: render it after the hash step"),
)
