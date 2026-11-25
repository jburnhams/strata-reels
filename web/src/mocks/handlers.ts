// The mock server: MSW handlers for every endpoint the app calls, answering like a healthy server.
// One set serves the jsdom tests (Node, tests/utils/server.ts), Storybook (a service worker,
// .storybook/preview.tsx) and the visual suite (Node, strata-visual.config.ts), so the app's real
// fetch, query and DataView code runs in all three and nothing is a module mock.
//
// The named handlers below each replace one route to put a test, story or screenshot into another
// state: `server.use(sourcesError)` in a test, `msw.use(sourcesError)` in a story's beforeEach,
// `states: { error: [sourcesError] }` in the visual config.
import { delay, HttpResponse, http } from 'msw'
import type { Chapter, Scenes, Source, WorkflowRun } from '../api'
import { initialSources, systemNearlyFull as nearlyFull, readSource, systemStatus } from './fixtures'
import {
  fresh,
  jobsPage,
  otherRuns,
  proxyWebm,
  tapeId,
  tapePeaks,
  tapeRun,
  tapeRunChecked,
  tapeRunDone,
  tapeRunFailed,
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
}

/** Back to the healthy state: call between tests and stories. */
export function resetMock() {
  mock.sources = [...initialSources]
  mock.jobs = structuredClone(jobsPage.items)
  mock.runs = {}
  mock.marks = {}
  mock.titles = {}
}

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
  runs: '/api/workflows/tape/subjects',
  run: '/api/workflows/tape/subjects/:subject',
  runAgain: '/api/workflows/tape/subjects/:subject/run',
  approve: '/api/workflows/tape/subjects/:subject/stages/:stage/approve',
} as const

const refused = (detail: string, status = 400) => HttpResponse.json({ detail }, { status })
const notFound = (detail: string) => HttpResponse.json({ detail }, { status: 404 })
const sourceOf = (id: unknown) => mock.sources.find(s => s.id === Number(id))
const down = () => new HttpResponse(null, { status: 503, statusText: 'Service Unavailable' })
/** A subject's source id, as `jobs.source_id_of` reads it (`source:5`, escaped or not). */
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
    if (params.stage !== 'check_chapters') return refused(`${String(params.stage)} is a stage, not a gate: it is run, not approved`)
    mock.runs[id] = tapeRunChecked
    return HttpResponse.json(fresh(mock.runs[id]))
  }),
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
/** A server installed from a wheel (no source commit) whose home folder's disk could not be read. */
export const systemUnread = http.get(at(paths.system), () => HttpResponse.json({ ...systemStatus, commit: null, folders: [] }))

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
