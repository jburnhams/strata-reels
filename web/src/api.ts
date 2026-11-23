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
/** `GET /api/system`: strata-python's SystemInfo plus this app's own numbers (src/app/system.py
 *  AppNumbers). Those are extra fields on the shared model, so the schema does not name them. */
export type SystemStatus = Schema<'SystemInfo'> & { sources: number; home_bytes: number }

export const api = createTypedApi<paths>()

/** The query keys the screens share, so a write can invalidate every read it changes. */
export const keys = {
  system: ['system'],
  sources: ['sources'],
} as const
