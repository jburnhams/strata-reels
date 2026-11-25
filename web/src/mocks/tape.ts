// Stand-in results for one tape (christmas-1996.mkv, source 5) as the server would answer them once
// every job has run: its scenes with thumbnails, descriptions and faces, its transcript, its waveform
// and its job queue. Written by hand to read like a real family tape (the descriptions keep what is
// seen apart from what is guessed, as strata-models' scene-describe-v2 does), and fixed, so the
// screenshots do not change. Pictures are SVG drawings of each scene (the colours are the picture's
// content, not interface colour); the browser copy is a two-second blank WebM, enough for the player
// to load (the poster, the scene's first thumbnail, is what shows).
import type { Job, JobsPage, Peaks, Scene, Scenes, Transcript, WorkflowRun } from '../api'
import { initialSources } from './fixtures'

const THUMB_STEP = 10

type Look = { wall: string; floor: string; thing: string; figure: string }
const looks: Look[] = [
  { wall: '#c9b48e', floor: '#7a5a3a', thing: '#2f6b3a', figure: '#b0413e' }, // the tree in the front room
  { wall: '#d8c9a8', floor: '#6d4c33', thing: '#e0d3b8', figure: '#3e5c8a' }, // presents on the rug
  { wall: '#dfe6ec', floor: '#f4f4f2', thing: '#5b6b7a', figure: '#2d4a2f' }, // snow in the garden
  { wall: '#b98d6a', floor: '#5a3b28', thing: '#efe8da', figure: '#7d3f6b' }, // the dinner table
  { wall: '#2a2a33', floor: '#1d1d24', thing: '#e8c35a', figure: '#9a8f86' }, // crackers with the lights low
  { wall: '#c4a98a', floor: '#6a4e36', thing: '#3a3a3a', figure: '#4f7a5b' }, // the sofa, after dinner
]

function frameSvg(look: Look, t: number): string {
  const x = 30 + ((t * 3) % 230)
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 240" width="320" height="240">
<rect width="320" height="240" fill="${look.wall}"/><rect y="160" width="320" height="80" fill="${look.floor}"/>
<rect x="200" y="70" width="70" height="95" rx="8" fill="${look.thing}"/>
<circle cx="${x}" cy="112" r="14" fill="#e2bc9a"/><rect x="${x - 13}" y="126" width="26" height="52" rx="7" fill="${look.figure}"/>
<rect y="226" width="320" height="14" fill="#ffffff18"/></svg>`
}

type Plan = {
  start: number
  end: number
  shots: number
  look: number
  confidence: number | null
  reasons: string[]
  faces: number | null
  speech: string | null
  description: Scene['description']
}

const seen = (
  summary: string,
  observed: string[],
  inferred: { guess: string; because: string }[],
  extra: Partial<NonNullable<Scene['description']>> = {},
): Scene['description'] => ({
  summary,
  observed,
  inferred,
  setting: 'indoor',
  people: 3,
  children: 1,
  activity: 'family gathering',
  tags: ['christmas'],
  picture: { quality: 'fair', problems: ['noisy'] },
  model: 'qwen3.5-9b-vl',
  ...extra,
})

const plan: Plan[] = [
  {
    start: 0,
    end: 412.4,
    shots: 6,
    look: 0,
    confidence: null,
    reasons: [],
    faces: 2,
    speech: 'Is it on? Is the red light on? Right, wave to the camera, wave to Grandma.',
    description: seen(
      'Two children stand by a decorated tree and wave at the camera.',
      ['a decorated tree with lights', 'two children in pyjamas', 'a woman sitting on a sofa'],
      [{ guess: 'Christmas morning', because: 'pyjamas and unopened presents under the tree' }],
      { people: 3, children: 2, activity: 'waving at the camera', tags: ['tree', 'children', 'morning'] },
    ),
  },
  {
    start: 418.0,
    end: 1934.2,
    shots: 14,
    look: 1,
    confidence: 0.91,
    reasons: ['pictures differ (0.61)', 'black at the cut'],
    faces: 4,
    speech: "Who's that one from? Read the label. To Tom, love from Auntie Jean. What do you say?",
    description: seen(
      'The children open presents on the rug while adults watch from the sofa.',
      ['wrapping paper on the floor', 'a boy holding a toy car', 'three adults on a sofa'],
      [{ guess: 'presents from relatives', because: 'a gift label is read aloud' }],
      { people: 5, children: 2, activity: 'opening presents', tags: ['presents', 'children', 'living room'] },
    ),
  },
  {
    start: 1934.2,
    end: 2401.0,
    shots: 3,
    look: 2,
    confidence: 0.58,
    reasons: ['pictures differ (0.81)'],
    faces: 1,
    speech: '',
    description: seen(
      'Someone films the snowy garden through the back door, then steps outside.',
      ['snow on a lawn and a fence', 'a child in a red coat'],
      [{ guess: 'the family’s own garden', because: 'filmed from the back door' }],
      {
        setting: 'outdoor',
        people: 1,
        children: 1,
        activity: 'playing in the snow',
        tags: ['snow', 'garden'],
        picture: { quality: 'poor', problems: ['overexposed', 'shaky'] },
      },
    ),
  },
  {
    start: 2412.6,
    end: 5210.0,
    shots: 22,
    look: 3,
    confidence: 0.88,
    reasons: ['pictures differ (0.66)', 'black between'],
    faces: 6,
    speech: "Pass the sprouts down. No, not to him, he won't eat them. Can everyone squeeze in for a picture?",
    description: seen(
      'The family eats Christmas dinner at a long table.',
      ['a roast on a platter', 'paper hats', 'eight people around a table'],
      [
        { guess: 'Christmas dinner', because: 'paper hats and crackers on the table' },
        { guess: 'at a grandparent’s house', because: 'a different room from the morning scenes' },
      ],
      { people: 8, children: 2, activity: 'eating dinner', tags: ['dinner', 'table', 'paper hats'] },
    ),
  },
  {
    start: 5210.0,
    end: 5642.8,
    shots: 5,
    look: 4,
    confidence: 0.47,
    reasons: ['pictures alike (0.84)'],
    faces: 3,
    speech: 'Pull it! Pull! What does the joke say?',
    description: null,
  },
  {
    start: 5650.0,
    end: 10812.4,
    shots: 31,
    look: 5,
    confidence: 0.95,
    reasons: ['pictures differ (0.52)', 'black at the cut'],
    faces: 3,
    speech: null,
    description: seen(
      'Adults talk on the sofa while the television plays; the camera is left running on a shelf.',
      ['a television showing a film', 'two adults asleep on the sofa'],
      [{ guess: 'the camera was left recording by mistake', because: 'the picture does not move for long stretches' }],
      {
        people: 4,
        children: 0,
        activity: 'watching television',
        tags: ['sofa', 'television', 'evening'],
        picture: { quality: 'poor', problems: ['dark', 'noisy'] },
      },
    ),
  },
]

const thumbName = (t: number) => `${String(Math.round(t / THUMB_STEP)).padStart(6, '0')}.jpg`

/** The SVG for a thumbnail by its name, or null when the tape has none by that name. */
export function thumbnailSvg(name: string): string | null {
  const m = /^(\d{6})\.jpg$/.exec(name)
  if (!m) return null
  const t = Number(m[1]) * THUMB_STEP
  const p = plan.find(x => t >= x.start && t < x.end) ?? (t < 10_812 ? plan[0] : null)
  return p ? frameSvg(looks[p.look], t) : null
}

export const tapeId = 5
const source = initialSources.find(s => s.id === tapeId)!

/** `GET /api/sources/5/scenes` with every job done. */
export const tapeScenes: Scenes = {
  source,
  analysis: {
    proxy: true,
    thumbnails: true,
    peaks: true,
    shots: 81,
    transcript: true,
    faces: true,
    described: 5,
    chapters: true,
    named: 3,
  },
  waiting: null,
  regrouping: false,
  chapters: [
    {
      index: 0,
      start: 0,
      end: 2401.0,
      scenes: [0, 1, 2],
      confidence: null,
      reasons: [],
      name: {
        title: 'Christmas morning',
        summary: 'The children wave by the tree, open their presents with the family watching, then play in the snowy garden.',
        inferred: [{ guess: 'Christmas Day', because: 'pyjamas, presents under a decorated tree and gift labels read aloud' }],
        tags: ['christmas', 'presents', 'snow'],
        mixed: false,
        model: 'qwen3.5-9b-vl',
      },
      title: null,
    },
    {
      index: 1,
      start: 2412.6,
      end: 5642.8,
      scenes: [3, 4],
      confidence: 0.97,
      reasons: ['pictures differ (0.58)', 'described differently', 'black between'],
      name: {
        title: 'Christmas dinner',
        summary: 'The family eats at a long table in paper hats, then pulls crackers and reads the jokes.',
        inferred: [{ guess: 'at a grandparent’s house', because: 'a different room from the morning scenes' }],
        tags: ['christmas', 'dinner', 'crackers'],
        mixed: false,
        model: 'qwen3.5-9b-vl',
      },
      title: null,
    },
    {
      index: 2,
      start: 5650.0,
      end: 10812.4,
      scenes: [5],
      confidence: 0.61,
      reasons: ['pictures differ (0.74)', 'described differently'],
      name: {
        title: 'The evening in front of the television',
        summary: 'Adults talk and doze on the sofa while a film plays, with the camera left running on a shelf.',
        inferred: [],
        tags: ['television', 'sofa', 'evening'],
        mixed: true,
        model: 'qwen3.5-9b-vl',
      },
      title: null,
    },
  ],
  scenes: plan.map((p, index) => {
    const frames = []
    for (let t = Math.ceil(p.start / THUMB_STEP) * THUMB_STEP; t < p.end; t += THUMB_STEP)
      frames.push({ time: t, url: `/api/sources/${tapeId}/thumbnails/${thumbName(t)}` })
    return {
      index,
      start: p.start,
      end: p.end,
      shots: p.shots,
      confidence: p.confidence,
      reasons: p.reasons,
      frames,
      description: p.description,
      faces: p.faces,
      speech: p.speech,
      mark: null,
    }
  }),
}

/** The same tape part way: shots grouped, nothing of step 3 yet but the faces, and the browser copy, the sound and
 *  the thumbnails not made yet (so the scenes have no pictures). */
export const tapeScenesPartWay: Scenes = {
  ...tapeScenes,
  analysis: {
    ...tapeScenes.analysis,
    proxy: false,
    peaks: false,
    thumbnails: false,
    transcript: false,
    described: 0,
    chapters: false,
    named: 0,
  },
  chapters: null,
  scenes: tapeScenes.scenes.map(s => ({ ...s, frames: [], description: null, speech: null })),
}

/** Another source before its scenes are grouped. */
export const waitingScenes = (id: number): Scenes => ({
  source: initialSources.find(s => s.id === id) ?? source,
  analysis: {
    proxy: true,
    thumbnails: true,
    peaks: true,
    shots: 12,
    transcript: false,
    faces: false,
    described: 0,
    chapters: false,
    named: 0,
  },
  waiting: 'Grouping the shots into scenes (the keyframes and scenes steps).',
  scenes: [],
  chapters: null,
  regrouping: false,
})

/** `GET /api/sources/5/transcript`: what is said, phrase by phrase, with one doubtful stretch of hiss. */
export const tapeTranscript: Transcript = {
  language: 'en',
  model: 'whisper-large-v3-turbo',
  waiting: null,
  segments: [
    ...plan.flatMap((p, i) =>
      (p.speech ?? '')
        .split(/(?<=[.?!])\s+/)
        .filter(Boolean)
        .map((text, j) => {
          const start = p.start + 4 + j * 6
          const words = text.split(' ')
          return {
            start,
            end: start + words.length * 0.4,
            text,
            doubtful: false,
            words: words.map((w, k) => ({
              start: start + k * 0.4,
              end: start + (k + 1) * 0.4,
              word: ` ${w}`,
              probability: i === 3 && k === 2 ? 0.41 : 0.93,
            })),
          }
        }),
    ),
    { start: 6000, end: 6004, text: 'Thank you.', doubtful: true, words: [] },
  ].sort((a, b) => a.start - b.start),
}

/** `GET /api/sources/5/peaks`: a camcorder's quiet sound (it peaks near a tenth of full scale), louder at dinner. */
export const tapePeaks: Peaks = (() => {
  const n = 4000
  const seconds = 10_812.4
  const min: number[] = []
  const max: number[] = []
  for (let i = 0; i < n; i++) {
    const t = (i / n) * seconds
    const p = plan.find(x => t >= x.start && t < x.end)
    const level = p === undefined ? 0.004 : p.look === 3 ? 0.11 : p.look === 5 ? 0.03 : 0.07
    const v = level * (0.55 + 0.45 * Math.abs(Math.sin(i / 3.7) * Math.cos(i / 17)))
    max.push(Math.round(v * 1e4) / 1e4)
    min.push(-Math.round(v * 0.9e4) / 1e4)
  }
  return { seconds, min, max }
})()

// Times before now, so "20 minutes ago" reads the same in every run of the screenshots. They are made when this
// module loads, which in the visual suite is minutes before the last page is shot, so the handlers move them on by
// the time since (`fresh`), or "33 minutes ago" turns into "34" part way through a run.
const loaded = Date.now()
const at = (minutesAgo: number) => new Date(loaded - minutesAgo * 60_000).toISOString()
const ISO = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$/

/** A copy of a fixture with every time in it moved on by the time since this module loaded. */
export function fresh<T>(value: T): T {
  const by = Date.now() - loaded
  return JSON.parse(JSON.stringify(value), (_k, v: unknown) =>
    typeof v === 'string' && ISO.test(v) ? new Date(Date.parse(v) + by).toISOString() : v,
  ) as T
}
/** A tape's job (`workflow:tape`, src/app/jobs.py): one per tape, advancing it through every stage. */
const job = (id: number, source: number, state: Job['state'], more: Partial<Job> = {}): Job => ({
  // the newest (highest id) was made last
  id,
  kind: 'workflow:tape',
  state,
  domain: `tape:source:${source}`,
  created_at: at(60 - id * 3),
  started_at: state === 'queued' ? null : at(59 - id * 3),
  finished_at: state === 'done' || state === 'failed' || state === 'cancelled' ? at(58 - id * 3) : null,
  error: null,
  attempts: state === 'queued' ? 0 : 1,
  progress: null,
  result: null,
  ...more,
})

const modelsDown = 'strata-models is not answering at its address: is the service running?'

/** `GET /api/jobs`: a job per tape, newest first: one running, one waiting, one failed. */
export const jobsPage = {
  items: [
    job(9, 4, 'running', { progress: { done: 5, total: 12, text: 'Find the shots', stage: 'shots', at: null } }),
    job(8, 3, 'queued'),
    job(7, 2, 'failed', { error: modelsDown, attempts: 5 }),
    job(6, 1, 'done'),
    job(5, tapeId, 'done'),
    job(4, 4, 'cancelled'),
  ],
  next: null,
  total: 6,
} satisfies JobsPage

/** The tape pipeline's stages (src/app/jobs.py `TAPE`): name, title, what it needs. */
const STAGES: [string, string, string[]][] = [
  ['hash', 'Read the whole file', []],
  ['proxy', 'Make a browser copy', ['hash']],
  ['thumbnails', 'Make thumbnails', ['hash']],
  ['peaks', 'Draw the sound', ['hash']],
  ['shots', 'Find the shots', ['hash']],
  ['keyframes', 'Take pictures of each shot', ['shots']],
  ['scenes', 'Group the shots into scenes', ['shots', 'keyframes']],
  ['faces', 'Find the faces', ['keyframes']],
  ['transcript', 'Write down what is said', ['hash']],
  ['describe', 'Describe the scenes', ['scenes', 'transcript']],
  ['chapters', 'Group the scenes into chapters', ['scenes', 'describe']],
  ['name_chapters', 'Name the chapters', ['chapters']],
  ['check_chapters', 'Check the chapters', ['name_chapters']],
]
/** The person's steps: approved, never run. */
const GATES = new Set(['check_chapters'])

type StageState = WorkflowRun['stages'][number]['state']

/** A tape's `WorkflowRun` with each stage done unless `states` says otherwise (the chapters' check waits for the person
 *  unless it says done); `errors` are the failed stages' sentences. */
export function tapeRun(
  source: number,
  states: Partial<Record<string, StageState>> = {},
  { job: running = null, errors = {} }: { job?: Job | null; errors?: Record<string, string> } = {},
): WorkflowRun {
  const stages = STAGES.map(([name, title, needs], i) => {
    const gate = GATES.has(name)
    const before = i > 0 ? (states[STAGES[i - 1][0]] ?? 'done') : 'done'
    const state = states[name] ?? (gate ? (before === 'done' ? 'waiting' : 'blocked') : 'done')
    const ran = state === 'done' || state === 'failed' || state === 'stale'
    return {
      name,
      title,
      gate,
      needs,
      state,
      last: ran
        ? {
            stage: name,
            status: state === 'failed' ? 'failed' : 'done',
            at: at(40 - i),
            elapsed_s: [212, 1480, 96, 31, 640, 75, 4, 58, 2210, 380, 1, 42][i] ?? 0,
            metrics: {},
            error: errors[name] ?? null,
            by: gate ? 'person' : null,
            data: null,
          }
        : null,
    }
  })
  const failed = stages.filter(s => s.state === 'failed').map(s => s.name)
  const waiting = stages.filter(s => s.state === 'waiting').map(s => s.name)
  const done = stages.every(s => s.state === 'done')
  return {
    pipeline: 'tape',
    subject: `source:${source}`,
    state: failed.length ? 'failed' : waiting.length ? 'waiting' : done ? 'done' : 'blocked',
    waiting,
    failed,
    stages,
    job: running,
  }
}

/** `GET /api/workflows/tape/subjects/source:5`: every stage done, by the tape's job; the chapters wait for the check. */
export const tapeRunDone = tapeRun(tapeId, {}, { job: job(5, tapeId, 'done') })
/** The same, the person having confirmed the chapters. */
export const tapeRunChecked = tapeRun(tapeId, { check_chapters: 'done' }, { job: job(5, tapeId, 'done') })
/** Part way: the browser copy, the sound and the descriptions still to come, the job on the copy. */
export const tapeRunPartWay = tapeRun(
  tapeId,
  {
    proxy: 'ready',
    thumbnails: 'blocked',
    peaks: 'blocked',
    transcript: 'blocked',
    describe: 'blocked',
    chapters: 'blocked',
    name_chapters: 'blocked',
  },
  { job: job(10, tapeId, 'running', { progress: { done: 1, total: 12, text: 'Make a browser copy', stage: 'proxy', at: null } }) },
)
/** The faces stage failed (strata-models down) and the job is waiting to try again; what does not need it went on. */
export const tapeRunFailed = tapeRun(
  tapeId,
  { faces: 'failed' },
  { job: job(10, tapeId, 'queued', { error: modelsDown, attempts: 2 }), errors: { faces: modelsDown } },
)

/** The browser copy: a two-second blank WebM, base64. */
export const proxyWebm = Uint8Array.from(
  atob(
    'GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQRChYECGFOAZwEAAAAAAAhIEU2bdLpNu4tTq4QVSalmU6yBoU27i1OrhBZUrmtTrIHYTbuMU6uEElTDZ1OsggGETbuMU6uEHFO7a1Osgggy7AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmsirXsYMPQkBNgI1MYXZmNjAuMTYuMTAwV0GNTGF2ZjYwLjE2LjEwMESJiECfYAAAAAAAFlSua0CmrgEAAAAAAAA414EBc8WIaJMcz4YErxOcgQAitZyDdW5kiIEAhoVWX1ZQOYOBASPjg4QL68IA4ImwgWC6gUiagQKuAQAAAAAAAFzXgQJzxYgytDlbYkCECZyBACK1nIN1bmSIgQCGhkFfT1BVU1aqg2MuoFa7hATEtACDgQLhkZ+BAbWIQL9AAAAAAABiZIEQY6KTT3B1c0hlYWQBATgBQB8AAAAAABJUw2dA2nNzoGPAgGfImkWjh0VOQ09ERVJEh41MYXZmNjAuMTYuMTAwc3PaY8CLY8WIaJMcz4YErxNnyKVFo4dFTkNPREVSRIeYTGF2YzYwLjMxLjEwMiBsaWJ2cHgtdnA5Z8ihRaOIRFVSQVRJT05Eh5MwMDowMDowMi4wMDAwMDAwMDAAc3PXY8CLY8WIMrQ5W2JAhAlnyKJFo4dFTkNPREVSRIeVTGF2YzYwLjMxLjEwMiBsaWJvcHVzZ8ihRaOIRFVSQVRJT05Eh5MwMDowMDowMi4wMDgwMDAwMDAAH0O2dUXI54EAo4uCAACACAvkuaC8hKOugQAAgIJJg0IABfAEdgA4JBwYQgAAMHAAAH1GN/mgBv//vpB/9icqGmEJZGfcAKOKggAVgAgHxrMOxqOKggApgAgHxrMOxqOKggA9gAgHxrMOxqOKggBRgAgHxrMOxqOKggBlgAgHxrMOxqOKggB5gAgHxrMOxqOKggCNgAgHxrMOxqOKggChgAgHxrMOxqOKggC1gAgHxrMOxqOKggDJgAgHxrMOxqOVgQDIAIYAQJKcAElAAAMgAABY/tYAo4qCAN2ACAfGsw7Go4qCAPGACAfGsw7Go4qCAQWACAfGsw7Go4qCARmACAfGsw7Go4qCAS2ACAfGsw7Go4qCAUGACAfGsw7Go4qCAVWACAfGsw7Go4qCAWmACAfGsw7Go4qCAX2ACAfGsw7Go4qCAZGACAfGsw7Go5WBAZAAhgBAkpwASsAAAyAAAFj+1gCjioIBpYAIB8azDsajioIBuYAIB8azDsajioIBzYAIB8azDsajioIB4YAIB8azDsajioIB9YAIB8azDsajioICCYAIB8azDsajioICHYAIB8azDsajioICMYAIB8azDsajioICRYAIB8azDsajioICWYAIB8azDsajlYECWACGAECSnABJwAADIAAAWP7WAKOKggJtgAgHxrMOxqOKggKBgAgHxrMOxqOKggKVgAgHxrMOxqOKggKpgAgHxrMOxqOKggK9gAgHxrMOxqOKggLRgAgHxrMOxqOKggLlgAgHxrMOxqOKggL5gAgHxrMOxqOKggMNgAgHxrMOxqOKggMhgAgHxrMOxqOVgQMgAIYAQJKcAEigAAMgAABY/tYAo4qCAzWACAfGsw7Go4qCA0mACAfGsw7Go4qCA12ACAfGsw7Go4qCA3GACAfGsw7Go4qCA4WACAfGsw7Go4qCA5mACAfGsw7Go4qCA62ACAfGsw7Go4qCA8GACAfGsw7Go4qCA9WACAfGsw7Go4qCA+mACAfGsw7Go5WBA+gAhgBAkpwAR4AAAyAAAFj+1gCjioID/YAIB8azDsajioIEEYAIB8azDsajioIEJYAIB8azDsajioIEOYAIB8azDsajioIETYAIB8azDsajioIEYYAIB8azDsajioIEdYAIB8azDsajioIEiYAIB8azDsajioIEnYAIB8azDsajioIEsYAIB8azDsajlYEEsACGAECSnABG4AADIAAAWP7WAKOKggTFgAgHxrMOxqOKggTZgAgHxrMOxqOKggTtgAgHxrMOxqOKggUBgAgHxrMOxqOKggUVgAgHxrMOxqOKggUpgAgHxrMOxqOKggU9gAgHxrMOxqOKggVRgAgHxrMOxqOKggVlgAgHxrMOxqOKggV5gAgHxrMOxqOVgQV4AIYAQJKcAEZAAAMgAABY/tYAo4qCBY2ACAfGsw7Go4qCBaGACAfGsw7Go4qCBbWACAfGsw7Go4qCBcmACAfGsw7Go4qCBd2ACAfGsw7Go4qCBfGACAfGsw7Go4qCBgWACAfGsw7Go4qCBhmACAfGsw7Go4qCBi2ACAfGsw7Go4qCBkGACAfGsw7Go5WBBkAAhgBAkpwARcAAAyAAAFj+1gCjioIGVYAIB8azDsajioIGaYAIB8azDsajioIGfYAIB8azDsajioIGkYAIB8azDsajioIGpYAIB8azDsajioIGuYAIB8azDsajioIGzYAIB8azDsajioIG4YAIB8azDsajioIG9YAIB8azDsajioIHCYAIB8azDsajlYEHCACGAECSnABFQAADIAAAWP7WAKOKggcdgAgHxrMOxqOKggcxgAgHxrMOxqOKggdFgAgHxrMOxqOKggdZgAgHxrMOxqOKggdtgAgHxrMOxqOKggeBgAgHxrMOxqOKggeVgAgHxrMOxqOKggepgAgHxrMOxqOKgge9gAgHxrMOxqCToYqCB9EACAfGsw7GdaKEAM3+YBxTu2uRu4+zgQC3iveBAfGCAmTwgRA=',
  ),
  c => c.charCodeAt(0),
)
