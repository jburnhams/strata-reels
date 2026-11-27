// Sample reels for the mock server (handlers.ts): one reel of the tape (source 5) with a current version, a proposal
// and a file, and one with only a proposal. Clips are on the tape's PAL frames (25 fps), as the planner cuts them.
import type { Clip, Reel, ReelDetail, Render, Revision } from '../api'

const at = (s: number) => `${Math.round(s * 25)}/25`

/** `n` clips starting `from` seconds into the tape, `gap` apart, each `length` seconds, laid end to end in the reel. */
export function clipsOf(n: number, from: number, gap: number, length: number, sourceId = 5): Clip[] {
  return Array.from({ length: n }, (_, i): Clip => {
    const start = from + i * gap
    return {
      id: `c${(sourceId * 1000 + Math.round(start)).toString(16)}`,
      source_id: sourceId,
      start: at(start),
      end: at(start + length),
      start_seconds: start,
      end_seconds: start + length,
      at_seconds: i * length,
      scene: at(start - 0.52),
      gain_db: 0,
      score: 0.9 - i * 0.04,
      reasons: i % 3 === 2 ? [] : i % 3 ? ['faces close up'] : ['speech', 'a new place'],
      locked: false,
      pinned: false,
    }
  })
}

export function revisionOf(id: number, reelId: number, state: Revision['state'], clips: Clip[], seed = 0): Revision {
  const duration = clips.reduce((s, c) => s + c.end_seconds - c.start_seconds, 0)
  return {
    id,
    reel_id: reelId,
    base_id: null,
    state,
    author: 'planner',
    accepted: state === 'current' ? '2026-11-20T10:05:00Z' : null,
    accepted_by: state === 'current' ? 'person' : null,
    created: `2026-11-20T10:0${id % 10}:00Z`,
    duration_seconds: duration,
    target_seconds: 120,
    rate: '25/1',
    planner: { name: 'baseline', version: 1, seed },
    clips,
    conflicts: [],
  }
}

/** The tape's reel: version 2 current, version 3 proposed. */
export const tapeReel: ReelDetail = {
  id: 1,
  name: 'christmas-1996.mkv, 2:00',
  source_id: 5,
  source_name: 'christmas-1996.mkv',
  target_seconds: 120,
  created: '2026-11-20T10:00:00Z',
  current_id: 2,
  current: revisionOf(2, 1, 'current', clipsOf(12, 40, 600, 10)),
  open: [revisionOf(3, 1, 'proposal', clipsOf(10, 95, 700, 12), 1)],
  proposals: 1,
  clips: 12,
  duration_seconds: 120,
  constraints: [],
}

/** A reel of side A with no version accepted yet. */
export const sideAReel: ReelDetail = {
  id: 2,
  name: 'Wedding highlights',
  source_id: 1,
  source_name: 'wedding-1994-side-a.mkv',
  target_seconds: 60,
  created: '2026-11-21T18:30:00Z',
  current_id: null,
  current: null,
  // The side has too little worth keeping for its length: the plan says what it could not do.
  open: [
    {
      ...revisionOf(5, 2, 'proposal', clipsOf(5, 300, 1200, 10, 1)),
      target_seconds: 60,
      conflicts: [{ override: null, message: 'The tape has 0:50 worth keeping, short of the 1:00 asked for.' }],
    },
  ],
  proposals: 1,
  clips: null,
  duration_seconds: null,
  constraints: [],
}

export const initialReels: ReelDetail[] = [tapeReel, sideAReel]

/** A reel in the list, as `GET /api/reels` answers it. */
export const listed = ({ current, open, constraints, ...reel }: ReelDetail): Reel => {
  void current
  void open
  void constraints
  return reel
}

const sha = 'e3f1a9c04b7d2e58a6c1f0b39d47e2a815c6d0b4f7a3e9c2d18b5f60a4c7e391'

/** A render as the server answers it, by its state. */
export function renderOf(id: number, rev: Revision, state: Render['state']): Render {
  const done = state === 'done'
  return {
    id,
    reel_id: rev.reel_id,
    revision_id: rev.id,
    job_id: 40 + id,
    state,
    error: state === 'failed' ? 'christmas-1996.mkv has changed since it was added: add it again' : null,
    created: '2026-11-20T10:06:00Z',
    finished: done ? '2026-11-20T10:09:30Z' : null,
    url: done ? `api/reels/${rev.reel_id}/renders/${id}/file` : null,
    sha256: done ? sha : null,
    bytes: done ? 182_400_000 : null,
    duration_seconds: done ? rev.duration_seconds : null,
    manifest: done ? { app: { revision: rev.id }, file: { sha256: sha } } : null,
  }
}

/** The tape's current version, rendered. */
export const initialRenders: Render[] = [renderOf(1, tapeReel.current as Revision, 'done')]
