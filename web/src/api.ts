// The server's endpoints, typed from the server itself. `./app openapi` writes web/openapi.json from the
// FastAPI routes, and `npm run api-types` (strata-react's strata-api-types) turns it into
// api.generated.ts, which is committed and never edited: a route or model change on the Python side
// shows up here as a type error, and CI fails a stale file on both sides (`app openapi --check`,
// `npm run api-types:check`).
//
// Reads go through useLoad and writes through useAsyncAction; both call `api`, a
// createTypedApi over the generated paths, so the path, its {params}, the query, the JSON body and the
// answer are all checked. It puts the app's base before every path (the server gives every page a
// relative <base> back to its root, so the app works behind a reverse proxy at /<prefix>/), and a
// non-2xx answer throws HttpError with the server's own sentence (`detail`) as its `reason`.
import { createTypedApi } from 'strata-react'
import type { paths, Schema } from './api.generated'

export type Source = Schema<'Described'>
export type Video = Schema<'Video'>
export type Folder = Schema<'Folder'>
export type Scenes = Schema<'Scenes'>
export type Scene = Schema<'Scene'>
export type Chapter = Schema<'Chapter'>
export type Transcript = Schema<'Transcript'>
export type Peaks = Schema<'Peaks'>
/** A tape's faces as the People tab shows them (src/app/people.py): groups of tracks to name, people named. */
export type People = Schema<'People'>
export type FaceGroup = Schema<'Group'>
/** One track of a group: its first still, how long it is on screen and when it starts. */
export type FaceMember = Schema<'Member'>
export type Person = Schema<'Person'>
/** Someone the face store knows already, to add a group to. */
export type Known = Schema<'Known'>
export type Job = Schema<'Task'>
export type JobsPage = Schema<'Page_Task_'>
/** A tape's way through its pipeline (`jobs.TAPE`), from strata-python's shared workflow routes. */
export type WorkflowRun = Schema<'WorkflowRun'>
/** `GET /api/system`: strata-python's SystemInfo plus this app's own numbers (src/app/system.py
 *  AppNumbers), merged into one schema by `add_system`. */
export type SystemStatus = Schema<'StrataReelsSystemInfo'>

/** A reel in the list, and one with its versions (src/app/reels.py). */
export type Reel = Schema<'ReelOut'>
export type ReelDetail = Schema<'ReelDetail'>
/** One version of a reel: its clips, and whether it is a proposal, the current one, past or rejected. */
export type Revision = Schema<'RevisionOut'>
export type Clip = Schema<'Clip'>
/** A version rendered to a file (src/app/renders.py): its job's state, then the file and its manifest. */
export type Render = Schema<'RenderOut'>

export const api = createTypedApi<paths>()

/** The query keys the screens share, so a write can invalidate every read it changes. */
export const keys = {
  system: ['system'],
  sources: ['sources'],
  scenes: (id: number) => ['sources', id, 'scenes'] as const,
  transcript: (id: number) => ['sources', id, 'transcript'] as const,
  peaks: (id: number) => ['sources', id, 'peaks'] as const,
  people: (id: number) => ['sources', id, 'people'] as const,
  known: ['people'],
  jobs: ['jobs'],
  run: (id: number) => ['sources', id, 'run'] as const,
  runs: ['sources', 'runs'],
  reels: ['reels'],
  reel: (id: number) => ['reels', id] as const,
  renders: (id: number) => ['reels', id, 'renders'] as const,
} as const

/** The tape pipeline's name for a source (src/app/jobs.py `subject_of`). */
export const subjectOf = (id: number) => `source:${String(id)}`

/** The queue's task states that mean a job has ended (strata-python's queue). */
export const FINISHED = ['done', 'failed', 'cancelled'] as const

/** Every tape's run (`GET /api/workflows/tape/subjects`), all pages, keyed by source id. */
export async function allRuns(): Promise<Map<number, WorkflowRun>> {
  const runs = new Map<number, WorkflowRun>()
  let after: string | undefined
  do {
    const page = await api.get('/api/workflows/tape/subjects', { query: { limit: 200, ...(after ? { after } : {}) } })
    for (const run of page.items) runs.set(Number(run.subject.replace(/^source:/, '')), run)
    after = page.next ?? undefined
  } while (after)
  return runs
}
