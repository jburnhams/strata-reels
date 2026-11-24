// The one question: "What happens on this tape, and where?" (R1 step 4, the tape's chapters). A
// family tape is hours of footage nobody has watched since it was recorded; the analysis steps split
// it into scenes (src/app/scenes.py) and say what each shows (describe.py), who is in it (faces) and
// what is said (speech.py). The finding counts the scenes and says what is still to come; then the
// player, the tape's sound, and the scenes in order, each a strip of its thumbnails to recognise it
// by, its description, and what the description is based on.
//
// - Every read is useLoad -> DataView. The scenes are one read (`GET /api/sources/{id}/scenes`,
//   src/app/browse.py), which says what each step has found so far, so a tape part way through its
//   steps shows what is done and names what is not, never an empty answer that reads as "nothing".
//   The waveform and the transcript are reads of their own, so a slow one never holds the scenes.
// - The tape's analysis is one strata-python pipeline (src/app/jobs.py `TAPE`), advanced by one queued
//   job. The page follows it with `useWorkflowRun` over the shared workflow route: while the job is
//   waiting or running the scenes are read again every few seconds, and everything once it ends, so a
//   tape opened part way fills in without a reload. The Steps tab is strata-react's `WorkflowSteps`
//   (each step's state as a word, a failure in the server's words with Try again) under the job's
//   `JobStatus` while it runs, and the finding names a failed step, so a stuck tape says why. The transcript's key
//   carries whether there is one, so it is read again when it arrives; the waveform is read when the
//   scenes first say it is there.
// - Playback is Strata's MediaPlayer over the browser copy (the proxy step's file), driven from the
//   page through `mediaRef`: a frame of a strip, a scene's Play button, a word of the transcript and
//   the waveform all seek it. The scenes are the player's spans and the waveform's chapter marks.
// - Only the scene being played is given the time, so the playhead re-draws one strip, not all of
//   them (a long tape has a hundred scenes); the time is kept to a quarter second for the same reason.
// - Tape sound is quiet (a camcorder peaks near a tenth of full scale), so the waveform is drawn
//   normalised and says by how much (strata-react's Waveform `normalise`).
// - The tab (scenes or transcript) and the open scene details are in the URL, so a link opens them.
// - Copy keeps what the describer saw apart from what it guessed, as its output does (spec S4), and
//   says how sure the cut before a scene is when it is unsure, since that is where a person corrects
//   it (the corrections, stored as overrides, are the next step).
import { Play } from 'lucide-react'
import { memo, useCallback, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router'
import {
  Button,
  DataView,
  Disclosure,
  EmptyState,
  FilmStrip,
  format,
  type Job,
  JobStatus,
  type MediaElementState,
  MediaPlayer,
  type MediaSpanData,
  PageHeader,
  Skeleton,
  Status,
  Tabs,
  TextLink,
  type TranscriptGroup,
  TranscriptView,
  useExpanded,
  useLoad,
  useWorkflowRun,
  Waveform,
  WorkflowSteps,
} from 'strata-react'
import { api, keys, type Scene, type Scenes, type Source, subjectOf, type Transcript, type WorkflowRun } from '../api'

/** Under this the cut before a scene is as likely a shot change as a new scene: say so. */
export const UNSURE = 0.65

/** "Scene 3". */
export const sceneName = (s: Pick<Scene, 'index'>) => `Scene ${s.index + 1}`

/** The page's finding: how many scenes over how long, then what is still to come. */
export function describeScenes(d: Scenes): string {
  if (d.scenes.length === 0) return d.waiting ?? 'No scenes found.'
  const length = d.scenes.reduce((a, s) => a + (s.end - s.start), 0)
  const parts = [`${format.count(d.scenes.length, 'scene')} over ${format.runningTime(length)}`]
  const undescribed = d.scenes.filter(s => s.description === null).length
  if (undescribed === d.scenes.length) parts.push('not described yet')
  else if (undescribed) parts.push(`${format.count(undescribed, 'scene')} not described yet`)
  const unsure = d.scenes.filter(s => s.confidence !== null && s.confidence < UNSURE).length
  if (unsure) parts.push(`${unsure} ${unsure === 1 ? 'start' : 'starts'} to check`)
  if (d.analysis.transcript === false) parts.push('speech still to come')
  return `${parts.join('; ')}.`
}

/** The task states that mean a job has ended (strata-python's queue). */
const FINISHED = ['done', 'failed', 'cancelled']

/** The finding's last sentence when a step failed: which, and where to read why. */
export function describeFailed(run: Pick<WorkflowRun, 'stages'>): string | null {
  const failed = run.stages.filter(s => s.state === 'failed')
  if (!failed.length) return null
  const names = failed.map(s => `"${s.title}"`).join(' and ')
  return `${names} failed: the Steps tab says why.`
}

/** "Up to 4 faces", "No faces", or null before the faces step. A scene's count is the most on one keyframe: a floor. */
export function describeFaces(n: number | null): string | null {
  if (n === null) return null
  return n === 0 ? 'No faces' : `Up to ${format.count(n, 'face')}`
}

/** The picture's shape: PAL and NTSC tapes are 4:3 shown, whatever their stored pixels; anything else its own. */
export function aspectOf(source: Source): `${number}/${number}` | undefined {
  const v = source.video
  if (!v?.width || !v.height) return undefined
  if (v.width === 720 && (v.height === 576 || v.height === 480)) return '4/3'
  return `${v.width}/${v.height}`
}

/** The transcript as TranscriptView takes it: one group per scene, phrases under the scene they start in. */
export function transcriptGroups(t: Transcript, scenes: Scene[]): TranscriptGroup[] {
  const groups = scenes.map(s => ({
    id: `scene-${s.index}`,
    title: sceneName(s),
    start: s.start,
    end: s.end,
    phrases: [] as TranscriptGroup['phrases'][number][],
  }))
  t.segments.forEach((seg, i) => {
    let g = 0
    for (let k = 0; k < scenes.length; k++) if (scenes[k].start <= seg.start) g = k
    const words = seg.words.length
      ? seg.words.map(w => ({
          text: w.word.trim(),
          start: w.start,
          end: w.end,
          ...(seg.doubtful ? { doubtful: true } : w.probability === null ? {} : { confidence: w.probability }),
        }))
      : [{ text: seg.text, start: seg.start, end: seg.end, doubtful: seg.doubtful }]
    groups[g]?.phrases.push({ id: `p${i}`, start: seg.start, end: seg.end, words })
  })
  return groups.filter(g => g.phrases.length)
}

/** How often the page reads the tape again while its jobs run. */
const FOLLOW = 5000

export function TapeScreen() {
  const id = Number(useParams().id)
  const run = useWorkflowRun({
    queryKey: keys.run(id),
    queryFn: () => api.get('/api/workflows/tape/subjects/{subject}', { path: { subject: subjectOf(id) } }),
    finished: FINISHED,
    every: FOLLOW,
    invalidates: [keys.scenes(id), keys.transcript(id), keys.peaks(id)],
  })
  const load = useLoad({
    queryKey: keys.scenes(id),
    queryFn: () => api.get('/api/sources/{source_id}/scenes', { path: { source_id: id } }),
    refetchInterval: run.running ? FOLLOW : false,
  })
  const failed = run.status === 'ready' ? describeFailed(run.data) : null
  return (
    <>
      <PageHeader
        title={load.status === 'ready' ? load.data.source.name : 'Tape'}
        scope="Scenes found on the tape, with what each shows"
        finding={load.status === 'ready' ? { text: [describeScenes(load.data), failed].filter(Boolean).join(' ') } : undefined}
      />
      <DataView
        state={load}
        isEmpty={d => d.scenes.length === 0}
        skeleton={<TapeSkeleton />}
        empty={
          <div className="grid gap-6">
            <EmptyState title="No scenes yet">
              The scenes appear here as the steps below finish; the <TextLink to="/jobs">jobs page</TextLink> shows every tape's.
            </EmptyState>
            <TapeSteps id={id} run={run} />
          </div>
        }
      >
        {d => <Tape data={d} run={run} />}
      </DataView>
    </>
  )
}

function TapeSkeleton() {
  return (
    <div aria-hidden className="grid gap-4">
      <Skeleton className="aspect-4/3 w-full max-w-2xl" />
      <Skeleton className="h-(--control-h) w-full" />
      {[0, 1, 2].map(i => (
        <div key={i} className="grid gap-2">
          <Skeleton className="h-(--control-h-sm) w-48" />
          <Skeleton className="aspect-32/3 w-full" />
        </div>
      ))}
    </div>
  )
}

/** The tape's steps (`WorkflowSteps`), under the job's progress while it runs. Try again queues the tape's job, which starts at the failed step. */
function TapeSteps({ id, run }: { id: number; run: Job<WorkflowRun> }) {
  return (
    <DataView state={run} isEmpty={() => false} empty={null} skeleton={<Skeleton className="h-64 w-full max-w-xl" />}>
      {r => (
        <div className="grid max-w-xl gap-4">
          {r.job && run.running && (
            <JobStatus
              label="Analysis"
              running
              words={{ running: 'Analysing', finished: 'Analysed', failed: 'Stopped' }}
              summary={r.job.progress?.text ?? 'Waiting for its turn'}
              current={r.job.progress?.done}
              total={r.job.progress?.total ?? undefined}
            />
          )}
          <WorkflowSteps
            label="Steps of the analysis"
            stages={r.stages}
            onRetry={() => api.post('/api/workflows/tape/subjects/{subject}/run', { path: { subject: subjectOf(id) } }).then(run.poll)}
          />
        </div>
      )}
    </DataView>
  )
}

function Tape({ data, run }: { data: Scenes; run: Job<WorkflowRun> }) {
  const { source, scenes, analysis } = data
  const player = useRef<MediaElementState | null>(null)
  // A quarter second is finer than any frame strip or transcript word needs, and React skips a render for the same value.
  const [time, setTime] = useState(0)
  const onTime = useCallback((t: number) => setTime(Math.floor(t * 4) / 4), [])
  const seek = useCallback((t: number, play = false) => {
    setTime(t)
    player.current?.seek(t)
    if (play) void player.current?.play()
  }, [])
  const spans = useMemo<MediaSpanData[]>(() => scenes.map(s => ({ start: s.start, end: s.end, kind: 'scene' })), [scenes])
  const spanKinds = useMemo(() => ({ scene: { label: 'Scene', behaviour: 'off' as const } }), [])
  const marks = useMemo(() => scenes.map(s => ({ time: s.start, label: sceneName(s), kind: 'chapter' as const })), [scenes])
  const playing = scenes.findIndex(s => time >= s.start && time < s.end)

  return (
    <div className="grid gap-8">
      <section aria-label="Player" className="grid gap-3">
        {analysis.proxy ? (
          <MediaPlayer
            label={`Play ${source.name}`}
            kind="video"
            src={api.url(`/api/sources/${source.id}/proxy`)}
            poster={scenes[0]?.frames[0] ? api.url(scenes[0].frames[0].url) : undefined}
            aspect={aspectOf(source)}
            spans={spans}
            spanKinds={spanKinds}
            mediaRef={player}
            onTimeUpdate={onTime}
            className="w-full max-w-2xl"
          />
        ) : (
          <EmptyState title="The browser copy is still being made">
            The tape plays here once the proxy step has made a copy the browser can seek in. The scenes below are ready to read.
          </EmptyState>
        )}
        {analysis.peaks && <TapeSound sourceId={source.id} time={time} onSeek={seek} marks={marks} />}
      </section>
      <Tabs
        param="view"
        label="Scenes or transcript"
        items={[
          { value: 'scenes', label: 'Scenes' },
          { value: 'transcript', label: 'Transcript' },
          { value: 'steps', label: 'Steps' },
        ]}
      >
        {view =>
          view === 'steps' ? (
            <TapeSteps id={source.id} run={run} />
          ) : view === 'transcript' ? (
            <TapeTranscript sourceId={source.id} ready={analysis.transcript} scenes={scenes} time={time} onSeek={seek} />
          ) : (
            <SceneList scenes={scenes} playing={playing} time={time} onSeek={seek} />
          )
        }
      </Tabs>
    </div>
  )
}

function TapeSound({
  sourceId,
  time,
  onSeek,
  marks,
}: {
  sourceId: number
  time: number
  onSeek: (t: number) => void
  marks: { time: number; label: string; kind: 'chapter' }[]
}) {
  const load = useLoad({
    queryKey: keys.peaks(sourceId),
    queryFn: () => api.get('/api/sources/{source_id}/peaks', { path: { source_id: sourceId } }),
  })
  return (
    <DataView
      state={load}
      isEmpty={p => p.max.length === 0}
      skeleton={<Skeleton className="h-(--control-h) w-full" />}
      empty={<p className="type-body-sm text-muted-foreground">The tape has no sound to draw.</p>}
    >
      {p => <Waveform label="Sound of the tape" peaks={p} duration={p.seconds} time={time} onSeek={onSeek} marks={marks} normalise />}
    </DataView>
  )
}

function SceneList({
  scenes,
  playing,
  time,
  onSeek,
}: {
  scenes: Scene[]
  playing: number
  time: number
  onSeek: (t: number, play?: boolean) => void
}) {
  const open = useExpanded({ param: 'scene' })
  return (
    <ol aria-label="Scenes" className="grid gap-6">
      {scenes.map(s => (
        <SceneItem
          key={s.index}
          scene={s}
          time={s.index === playing ? time : undefined}
          open={open.isOpen(String(s.index))}
          onOpenChange={o => open.setOpen(String(s.index), o)}
          onSeek={onSeek}
        />
      ))}
    </ol>
  )
}

// Memoised: only the scene being played gets a `time`, so the playhead re-draws one scene.
const SceneItem = memo(function SceneItem({
  scene,
  time,
  open,
  onOpenChange,
  onSeek,
}: {
  scene: Scene
  time: number | undefined
  open: boolean
  onOpenChange: (open: boolean) => void
  onSeek: (t: number, play?: boolean) => void
}) {
  const name = sceneName(scene)
  const d = scene.description
  const faces = describeFaces(scene.faces)
  const headingId = `scene-${scene.index}-heading`
  return (
    <li aria-labelledby={headingId} aria-current={time !== undefined ? 'time' : undefined} className="grid min-w-0 gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 id={headingId} className="type-heading">
          {name}
        </h3>
        <p className="type-body-sm tabular-nums text-muted-foreground">
          {format.runningTime(scene.start)} to {format.runningTime(scene.end)}, {format.runningTime(scene.end - scene.start)} long
        </p>
      </div>
      <FilmStrip
        label={`Frames of ${name.toLowerCase()}`}
        frames={scene.frames.map(f => ({ time: f.time, src: api.url(f.url) }))}
        span={[scene.start, scene.end]}
        time={time}
        onSeek={t => onSeek(t, true)}
        missing="No picture"
      />
      <p className="type-body">{d ? d.summary : 'Not described yet.'}</p>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 type-body-sm text-muted-foreground">
        <Button size="sm" variant="outline" onClick={() => onSeek(scene.start, true)}>
          <Play aria-hidden />
          Play {name.toLowerCase()}
        </Button>
        <span>{format.count(scene.shots, 'shot')}</span>
        {faces && <span>{faces}</span>}
        {scene.confidence !== null && scene.confidence < UNSURE && (
          <Status tone="warning">{`Start unsure (${format.share(scene.confidence)})`}</Status>
        )}
      </div>
      {scene.speech ? (
        <blockquote className="border-l-2 border-border pl-3 type-body-sm text-muted-foreground">“{scene.speech}”</blockquote>
      ) : null}
      <Disclosure summary={`What ${name.toLowerCase()} shows, and why`} open={open} onOpenChange={onOpenChange}>
        <SceneDetail scene={scene} />
      </Disclosure>
    </li>
  )
})

function SceneDetail({ scene }: { scene: Scene }) {
  const d = scene.description
  return (
    <dl className="grid gap-3 type-body-sm sm:grid-cols-2">
      {d ? (
        <>
          <div>
            <dt className="type-label">Seen</dt>
            <dd>
              <ul className="list-disc pl-5">
                {d.observed.map(o => (
                  <li key={o}>{o}</li>
                ))}
              </ul>
            </dd>
          </div>
          <div>
            <dt className="type-label">Guessed</dt>
            <dd>
              {d.inferred.length ? (
                <ul className="list-disc pl-5">
                  {d.inferred.map(g => (
                    <li key={g.guess}>
                      {g.guess}, because {g.because}
                    </li>
                  ))}
                </ul>
              ) : (
                'Nothing beyond what is seen.'
              )}
            </dd>
          </div>
          <div>
            <dt className="type-label">People</dt>
            <dd>
              {format.count(d.people, 'person', 'people')}, {format.count(d.children, 'child', 'children')};{' '}
              {d.setting === 'unknown' ? 'indoors or out not known' : d.setting}
            </dd>
          </div>
          <div>
            <dt className="type-label">Picture</dt>
            <dd>
              {d.picture.quality[0].toUpperCase() + d.picture.quality.slice(1)}
              {d.picture.problems.length ? `: ${d.picture.problems.join(', ')}` : ''}
            </dd>
          </div>
        </>
      ) : (
        <div>
          <dt className="type-label">Description</dt>
          <dd>Not written yet: the describe job writes one for each scene.</dd>
        </div>
      )}
      <div>
        <dt className="type-label">Where it starts</dt>
        <dd>
          {scene.confidence === null
            ? 'At the start of the tape.'
            : `${format.share(scene.confidence)} sure a new scene starts here: ${scene.reasons.join(', ')}.`}
        </dd>
      </div>
      {d && (
        <div>
          <dt className="type-label">Written by</dt>
          <dd className="font-mono">{d.model ?? format.missing}</dd>
        </div>
      )}
    </dl>
  )
}

function TapeTranscript({
  sourceId,
  ready,
  scenes,
  time,
  onSeek,
}: {
  sourceId: number
  /** The scenes' word on the transcript: a change reads it again. */
  ready: boolean | null
  scenes: Scene[]
  time: number
  onSeek: (t: number, play?: boolean) => void
}) {
  const load = useLoad({
    queryKey: [...keys.transcript(sourceId), ready],
    queryFn: () => api.get('/api/sources/{source_id}/transcript', { path: { source_id: sourceId } }),
  })
  return (
    <DataView
      state={load}
      isEmpty={t => t.segments.length === 0}
      skeleton={<Skeleton className="h-48 w-full" />}
      empty={
        <EmptyState title={load.status === 'ready' && load.data.waiting ? 'No transcript yet' : 'Nothing is said on this tape'}>
          {load.status === 'ready' && load.data.waiting
            ? load.data.waiting
            : 'The transcript job found no speech, or the tape has no sound.'}
        </EmptyState>
      }
    >
      {t => (
        <TranscriptView label="Transcript of the tape" groups={transcriptGroups(t, scenes)} time={time} onSeek={at => onSeek(at, true)} />
      )}
    </DataView>
  )
}
