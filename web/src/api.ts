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
export type Job = Schema<'Task'>
export type JobsPage = Schema<'Page_Task_'>
/** A tape's way through its pipeline (`jobs.TAPE`), from strata-python's shared workflow routes. */
export type WorkflowRun = Schema<'WorkflowRun'>
/** `GET /api/system`: strata-python's SystemInfo plus this app's own numbers (src/app/system.py
 *  AppNumbers). Those are extra fields on the shared model, so the schema does not name them. */
export type SystemStatus = Schema<'SystemInfo'> & { sources: number; home_bytes: number }

export const api = createTypedApi<paths>()

/** The query keys the screens share, so a write can invalidate every read it changes. */
export const keys = {
  system: ['system'],
  sources: ['sources'],
  scenes: (id: number) => ['sources', id, 'scenes'] as const,
  transcript: (id: number) => ['sources', id, 'transcript'] as const,
  peaks: (id: number) => ['sources', id, 'peaks'] as const,
  jobs: ['jobs'],
  run: (id: number) => ['sources', id, 'run'] as const,
  runs: ['sources', 'runs'],
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
