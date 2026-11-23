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

/** What the mock server holds. Writes change it, so a test can add a source and see it listed. */
export const mock = { sources: [...initialSources] as Source[] }

/** Back to the healthy state: call between tests and stories. */
export function resetMock() {
  mock.sources = [...initialSources]
}

// Matched as paths, so they answer whatever origin and base the app runs under.
const at = (path: string) => `*${path}`

/** The endpoints, as the server's paths (src/app/server/app.py; the same keys as api.generated.ts). */
export const paths = {
  health: '/api/health',
  system: '/api/system',
  sources: '/api/sources',
} as const

const refused = (detail: string, status = 400) => HttpResponse.json({ detail }, { status })
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
