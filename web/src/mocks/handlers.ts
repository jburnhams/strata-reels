// The mock server: MSW handlers for every endpoint the app calls, answering like a healthy server.
// One set serves the jsdom tests (Node, tests/utils/server.ts), Storybook (a service worker,
// .storybook/preview.tsx) and the visual suite (Node, strata-visual.config.ts), so the app's real
// fetch, query and DataView code runs in all three and nothing is a module mock.
//
// The named handlers below each replace one route to put a test, story or screenshot into another
// state: `server.use(sourcesError)` in a test, `msw.use(sourcesError)` in a story's beforeEach,
// `states: { error: [sourcesError] }` in the visual config.
import { delay, HttpResponse, http } from 'msw'
import type { Source } from '../api'
import { initialSources, systemNearlyFull as nearlyFull, readSource, systemStatus } from './fixtures'
import { jobsPage, proxyWebm, tapeId, tapePeaks, tapeScenes, tapeScenesPartWay, tapeTranscript, thumbnailSvg, waitingScenes } from './tape'

/** What the mock server holds. Writes change it, so a test can add a source and see it listed. */
export const mock = { sources: [...initialSources] as Source[], jobs: structuredClone(jobsPage.items) }

/** Back to the healthy state: call between tests and stories. */
export function resetMock() {
  mock.sources = [...initialSources]
  mock.jobs = structuredClone(jobsPage.items)
}

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
  cancel: '/api/jobs/:id/cancel',
  retry: '/api/jobs/:id/retry',
} as const

const refused = (detail: string, status = 400) => HttpResponse.json({ detail }, { status })
const notFound = (detail: string) => HttpResponse.json({ detail }, { status: 404 })
const sourceOf = (id: unknown) => mock.sources.find(s => s.id === Number(id))
const down = () => new HttpResponse(null, { status: 503, statusText: 'Service Unavailable' })

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
    return HttpResponse.json(found.id === tapeId ? tapeScenes : waitingScenes(found.id))
  }),
  http.get(at(paths.transcript), ({ params }) =>
    Number(params.id) === tapeId
      ? HttpResponse.json(tapeTranscript)
      : HttpResponse.json({ language: null, model: null, waiting: 'Listening for speech (the transcript job).', segments: [] }),
  ),
  http.get(at(paths.peaks), ({ params }) =>
    Number(params.id) === tapeId ? HttpResponse.json(tapePeaks) : notFound('the waveform is not made yet'),
  ),
  http.get(at(paths.proxy), () => new HttpResponse(proxyWebm, { headers: { 'Content-Type': 'video/webm' } })),
  http.get(at(paths.thumbnail), ({ params }) => {
    const svg = thumbnailSvg(String(params.name))
    return svg ? new HttpResponse(svg, { headers: { 'Content-Type': 'image/svg+xml' } }) : notFound(`no thumbnail ${String(params.name)}`)
  }),
  http.get(at(paths.jobs), ({ request }) => {
    const states = new URL(request.url).searchParams.getAll('state')
    const items = states.length ? mock.jobs.filter(j => states.includes(j.state)) : mock.jobs
    return HttpResponse.json({ items, next: null, total: items.length })
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
/** The tape before its scenes are grouped. */
export const scenesWaiting = http.get(at(paths.scenes), () => HttpResponse.json(waitingScenes(tapeId)))
export const transcriptWaiting = http.get(at(paths.transcript), () =>
  HttpResponse.json({ language: null, model: null, waiting: 'Listening for speech (the transcript job).', segments: [] }),
)
export const peaksLoading = http.get(at(paths.peaks), () => delay('infinite'))
export const jobsLoading = http.get(at(paths.jobs), () => delay('infinite'))
export const jobsEmpty = http.get(at(paths.jobs), () => HttpResponse.json({ items: [], next: null, total: 0 }))
export const jobsError = http.get(at(paths.jobs), down)
