// The one question: "What happens on this tape, and where?" (R1 step 4, the tape's chapters). A
// family tape is hours of footage nobody has watched since it was recorded; the analysis steps split
// it into scenes (src/app/scenes.py), say what each shows (describe.py), who is in it (faces) and
// what is said (speech.py), then group the scenes into chapters, one occasion each, and name them
// (chapters.py). The finding counts the chapters and scenes and says what is still to come; then the
// player, the tape's sound, and the chapters in order, each its name, what happens in it and its
// scenes, each scene a strip of its thumbnails to recognise it by, its description, and what the
// description is based on.
// - A chapter is the level a person skims at ("Christmas morning, then dinner"), so its name and
//   summary lead and its scenes follow under it. Before the chapters are grouped the scenes are one
//   list, as before; a chapter not named yet is "Chapter 2" with its span. What the namer guessed is
//   kept apart from what the scenes show, and a doubtful chapter (its start unsure, or its scenes
//   looking like more than one occasion) says so, since that is where a person corrects it.
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
//   the waveform all seek it. The scenes are the player's spans; the chapters are its seek bar's marks
//   and the waveform's, and the contents under it (strata-react's `MediaContents`), the one playing
//   marked, so a person sees which occasion they are in and jumps to another in one press. The seek bar
//   marks the scenes too (fainter), each named on hover, and the player's skip buttons step by scene and
//   by chapter, and a frame at a time at the tape's rate (strata-react #387). Stacked on a narrower screen,
//   a playing tape floats in a corner once the player scrolls off the top, so the picture stays in sight.
// - On a wide screen the player, its sound and the contents stay in a column of their own beside the
//   chapters and scenes (sticky, as tall as the window), so the picture is in sight while a person reads
//   and presses down a long tape; narrower, the column stacks above as before. The scene being played
//   is marked in the list by the bar the contents and the transcript use.
// - Only the scene being played is given the time, so the playhead re-draws one strip, not all of
//   them (a long tape has a hundred scenes); the time is kept to a quarter second for the same reason.
// - Tape sound is quiet (a camcorder peaks near a tenth of full scale), so the waveform is drawn
//   normalised and says by how much (strata-react's Waveform `normalise`).
// - The tab (scenes, people, transcript or steps) and the open scene details are in the URL, so a link opens them.
// - Copy keeps what the describer saw apart from what it guessed, as its output does (spec S4), and
//   says how sure the cut before a scene is when it is unsure, since that is where a person corrects
//   it.
// - The person corrects the chapters where they read them (overview §6, the corrections are overrides,
//   never edits of a result): "Start a chapter at scene 4" on a scene inside a chapter, "Join chapter 3
//   to chapter 2" on a chapter, and a title of their own (a Popover with one field). A mark is one of
//   the tape's inputs, so it queues the tape's job, which groups the chapters again and names only the
//   chapters that changed; meanwhile the page says it is regrouping and follows the job. Each mark shows
//   on its scene with Undo mark, which hands the boundary back to the grouping. A title is laid over the
//   namer's at once, and the namer's stays in sight under it.
// - Who is in each scene comes from the People tab (TapePeople.tsx): the person names the tape's faces a group at a
//   time, and the descriptions wait for that (the pipeline's `name_people` gate), so they can say who does what. A
//   scene shows the people named in it in place of its face count, and its detail lists who does what; until the
//   people are named the finding points to the People tab.
// - "Confirm the chapters" is the pipeline's `check_chapters` gate (strata-python's approve route), the
//   view's one filled button while the tape waits for it: it says the chapters are right, which the
//   weights are later fitted on (`./app fit-chapters`). A regroup after it asks again.
import { useQueryClient } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { type FormEvent, memo, type ReactNode, useCallback, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router'
import {
  AsyncButton,
  Button,
  cn,
  DataView,
  Disclosure,
  EmptyState,
  Field,
  FilmStrip,
  format,
  Input,
  type Job,
  JobStatus,
  MediaContents,
  type MediaContentsItem,
  type MediaElementState,
  type MediaMark,
  MediaPlayer,
  type MediaSpanData,
  PageHeader,
  Popover,
  Skeleton,
  Status,
  Tabs,
  TextLink,
  type TranscriptGroup,
  TranscriptView,
  useAsyncAction,
  useExpanded,
  useLoad,
  useWorkflowRun,
  Waveform,
  type WaveformMark,
  WorkflowSteps,
} from 'strata-react'
import {
  api,
  type Chapter,
  FINISHED,
  keys,
  type Scene,
  type Scenes,
  type Source,
  subjectOf,
  type Transcript,
  type WorkflowRun,
} from '../api'
import { framesPerSecond } from './SourcesScreen'
import { TapePeople } from './TapePeople'

/** Under this the cut before a scene is as likely a shot change as a new scene: say so. */
export const UNSURE = 0.65

/** "Scene 3". */
export const sceneName = (s: Pick<Scene, 'index'>) => `Scene ${s.index + 1}`

/** A chapter's title: the person's, else the namer's, else none yet. */
export const chapterTitle = (c: Pick<Chapter, 'name' | 'title'>) => c.title ?? c.name?.title ?? null

/** "Chapter 2: Christmas dinner", or "Chapter 2" before it is named. */
export const chapterName = (c: Pick<Chapter, 'index' | 'name' | 'title'>) => {
  const title = chapterTitle(c)
  return `Chapter ${c.index + 1}${title ? `: ${title}` : ''}`
}

/** What the person's mark on a scene says, or null for none. */
export function describeMark(mark: boolean | null | undefined): string | null {
  if (mark === true) return 'You started a chapter here'
  if (mark === false) return 'You kept this with the chapter before'
  return null
}

/** The chapters' check (the `check_chapters` gate) as the page tells it: to do, done, or nothing to say yet. */
export function checkOf(run: Pick<WorkflowRun, 'stages'>): { state: 'waiting' } | { state: 'done'; at: string } | null {
  const gate = run.stages.find(s => s.name === 'check_chapters')
  if (gate?.state === 'waiting') return { state: 'waiting' }
  if (gate?.state === 'done' && gate.last) return { state: 'done', at: gate.last.at }
  return null
}

/** The page's finding: how many chapters and scenes over how long, then what is still to come. */
export function describeScenes(d: Scenes): string {
  if (d.scenes.length === 0) return d.waiting ?? 'No scenes found.'
  const length = d.scenes.reduce((a, s) => a + (s.end - s.start), 0)
  const counted = format.count(d.scenes.length, 'scene')
  const parts = [
    d.chapters?.length
      ? `${format.count(d.chapters.length, 'chapter')} of ${counted} over ${format.runningTime(length)}`
      : `${counted} over ${format.runningTime(length)}`,
  ]
  const undescribed = d.scenes.filter(s => s.description === null).length
  if (undescribed === d.scenes.length) parts.push('not described yet')
  else if (undescribed) parts.push(`${format.count(undescribed, 'scene')} not described yet`)
  const unsure = d.scenes.filter(s => s.confidence !== null && s.confidence < UNSURE).length
  if (unsure) parts.push(`${unsure} ${unsure === 1 ? 'start' : 'starts'} to check`)
  if (d.analysis.transcript === false) parts.push('speech still to come')
  if (!d.analysis.cast && d.scenes.some(s => s.description === null)) parts.push('people to name in the People tab first')
  if (d.chapters === null) parts.push('chapters still to come')
  else if (d.regrouping) parts.push('grouping the chapters again by your marks')
  else if (d.chapters.some(c => c.name === null)) parts.push('chapter names still to come')
  return `${parts.join('; ')}.`
}

/** The finding's last sentence when a step failed: which, and where to read why. */
export function describeFailed(run: Pick<WorkflowRun, 'stages'>): string | null {
  const failed = run.stages.filter(s => s.state === 'failed')
  if (!failed.length) return null
  const names = failed.map(s => `"${s.title}"`).join(' and ')
  return `${names} failed: the Steps tab says why.`
}

/** The tape's job in a few words: the step it is on, or that it waits to try a failed step again, or for its turn. */
export function describeJobNow(job: Pick<NonNullable<WorkflowRun['job']>, 'state' | 'attempts' | 'progress'>): string {
  if (job.state === 'running') return job.progress?.text ?? 'Starting'
  return job.attempts > 0 ? 'Trying the failed step again shortly' : 'Waiting for its turn'
}

/** "Up to 4 faces", "No faces", or null before the faces step. A scene's count is the most on one keyframe: a floor. */
export function describeFaces(n: number | null): string | null {
  if (n === null) return null
  return n === 0 ? 'No faces' : `Up to ${format.count(n, 'face')}`
}

/** The people named in a scene, longest on screen first: "Tom, Ella and Grandma Rose". */
export function describeCast(people: NonNullable<Scene['people']>): string {
  const names = people.map(p => p.name ?? 'someone unnamed')
  return names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : names[0]
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

/** The tape's frame rate for the player's frame steps (the proxy keeps it: deinterlaced a frame at a time), or none. */
export function frameRate(source: Source): number | undefined {
  return framesPerSecond(source.video?.rate ?? null) ?? undefined
}

/** The waveform's marks: each chapter's start once there are chapters (the scenes are the player's spans), else each scene's. */
export function soundMarks(scenes: Scene[], chapters: Chapter[] | null): WaveformMark[] {
  if (chapters?.length) return chapters.map(c => ({ time: c.start, label: chapterName(c), kind: 'chapter' }))
  return scenes.map(s => ({ time: s.start, label: sceneName(s), kind: 'chapter' }))
}

/**
 * The seek bar's marks, which are also what the player's skip buttons step through: each chapter after the first (the first
 * is the bar's start) and each scene that does not start a chapter, as a fainter scene mark. So Next scene and Next chapter
 * skim the tape as a person would; a tape with no chapters yet skips by scene and by a minute.
 */
export function seekMarks(scenes: Scene[], chapters: Chapter[] | null): MediaMark[] {
  const starts = new Set((chapters ?? []).map(c => c.start))
  return [
    ...(chapters ?? []).filter(c => c.start > 0).map(c => ({ time: c.start, label: chapterName(c), kind: 'chapter' as const })),
    ...scenes.filter(s => s.start > 0 && !starts.has(s.start)).map(s => ({ time: s.start, label: sceneName(s), kind: 'scene' as const })),
  ].sort((a, b) => a.time - b.time)
}

/** The chapters as the contents beside the player: each one's name, span, and its scenes counted. */
export function chapterContents(chapters: Chapter[] | null): MediaContentsItem[] {
  return (chapters ?? []).map(c => ({
    id: String(c.index),
    title: chapterName(c),
    start: c.start,
    end: c.end,
    detail: format.count(c.scenes.length, 'scene'),
  }))
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
        scope="The tape's chapters and scenes, with what each shows and says"
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
              summary={describeJobNow(r.job)}
              current={r.job.progress?.done}
              total={r.job.progress?.total ?? undefined}
            />
          )}
          <WorkflowSteps
            label="Steps of the analysis"
            stages={r.stages}
            onRetry={() => api.post('/api/workflows/tape/subjects/{subject}/run', { path: { subject: subjectOf(id) } }).then(run.poll)}
            onApprove={stage =>
              api
                .post('/api/workflows/tape/subjects/{subject}/stages/{stage}/approve', {
                  path: { subject: subjectOf(id), stage: stage.name },
                  json: { value: null },
                })
                .then(run.poll)
            }
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
  const marks = useMemo(() => soundMarks(scenes, data.chapters), [scenes, data.chapters])
  const barMarks = useMemo(() => seekMarks(scenes, data.chapters), [scenes, data.chapters])
  const contents = useMemo(() => chapterContents(data.chapters), [data.chapters])
  // Pressing a chapter in the contents plays it; where the player stays in sight beside the list (its column is sticky on
  // a wide screen) the chapter's heading is also brought into view. Stacked, scrolling would take the player away.
  const column = useRef<HTMLElement>(null)
  const showChapter = useCallback((id: string) => {
    if (column.current && getComputedStyle(column.current).position === 'sticky')
      document.getElementById(`chapter-${id}-heading`)?.scrollIntoView({ block: 'start' })
  }, [])
  const playing = scenes.findIndex(s => time >= s.start && time < s.end)
  // A correction answers the scenes as they are now: shown at once, and the run read again, since a mark queues the job.
  const queryClient = useQueryClient()
  const { poll } = run
  const corrections = useMemo<Corrections>(() => {
    const shown = (d: Scenes) => {
      queryClient.setQueryData(keys.scenes(source.id), d)
      poll()
    }
    return {
      mark: (scene, start) =>
        api
          .put('/api/sources/{source_id}/scenes/{scene}/chapter-mark', { path: { source_id: source.id, scene }, json: { start } })
          .then(shown),
      rename: (scene, title) =>
        api
          .put('/api/sources/{source_id}/scenes/{scene}/chapter-title', { path: { source_id: source.id, scene }, json: { title } })
          .then(shown),
    }
  }, [queryClient, poll, source.id])

  return (
    <div className="grid gap-8 xl:grid-cols-2 xl:items-start">
      <section ref={column} aria-label="Player" className="grid min-w-0 gap-3 xl:sticky xl:top-0 xl:max-h-dvh xl:overflow-y-auto xl:py-2">
        {analysis.proxy ? (
          <MediaPlayer
            label={`Play ${source.name}`}
            kind="video"
            src={api.url(`/api/sources/${source.id}/proxy`)}
            poster={scenes[0]?.frames[0] ? api.url(scenes[0].frames[0].url) : undefined}
            aspect={aspectOf(source)}
            spans={spans}
            spanKinds={spanKinds}
            marks={barMarks}
            fps={frameRate(source)}
            float
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
        {contents.length > 0 && (
          <MediaContents
            label="Chapters of the tape"
            items={contents}
            time={time}
            onSeek={t => seek(t, true)}
            onSelect={showChapter}
            className="max-w-2xl"
          />
        )}
      </section>
      <Tabs
        className="min-w-0"
        param="view"
        label="Scenes, people or transcript"
        items={[
          { value: 'scenes', label: 'Scenes' },
          { value: 'people', label: 'People' },
          { value: 'transcript', label: 'Transcript' },
          { value: 'steps', label: 'Steps' },
        ]}
      >
        {view =>
          view === 'people' ? (
            <TapePeople id={source.id} run={run} onSeek={seek} />
          ) : view === 'steps' ? (
            <TapeSteps id={source.id} run={run} />
          ) : view === 'transcript' ? (
            <TapeTranscript sourceId={source.id} ready={analysis.transcript} scenes={scenes} time={time} onSeek={seek} />
          ) : (
            <div className="grid gap-8">
              {data.chapters?.length ? <ChaptersCheck id={source.id} run={run} regrouping={data.regrouping} /> : null}
              <SceneList scenes={scenes} chapters={data.chapters} playing={playing} time={time} onSeek={seek} corrections={corrections} />
            </div>
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
  marks: WaveformMark[]
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
  chapters,
  playing,
  time,
  onSeek,
  corrections,
}: {
  scenes: Scene[]
  chapters: Chapter[] | null
  playing: number
  time: number
  onSeek: (t: number, play?: boolean) => void
  corrections: Corrections
}) {
  const open = useExpanded({ param: 'scene' })
  const list = (label: string, of: Scene[], marking: boolean) => (
    <ol aria-label={label} className="grid gap-6">
      {of.map((s, i) => (
        <SceneItem
          key={s.index}
          scene={s}
          time={s.index === playing ? time : undefined}
          open={open.isOpen(String(s.index))}
          onOpenChange={o => open.setOpen(String(s.index), o)}
          onSeek={onSeek}
          // A scene inside a chapter can start one; the first of a chapter is joined from the chapter's header.
          onMark={marking && s.index > 0 && (i > 0 || s.mark !== null) ? corrections.mark : undefined}
        />
      ))}
    </ol>
  )
  if (!chapters?.length) return list('Scenes', scenes, false)
  return (
    <ol aria-label="Chapters" className="grid gap-12">
      {chapters.map(c => (
        <ChapterItem key={c.index} chapter={c} onSeek={onSeek} first={scenes[c.scenes[0]]} corrections={corrections}>
          {list(`Scenes of ${chapterName(c).toLowerCase()}`, c.scenes.map(i => scenes[i]).filter(Boolean), true)}
        </ChapterItem>
      ))}
    </ol>
  )
}

/** The person's corrections, each answering the scenes as they are after it. */
type Corrections = {
  /** A chapter starts at the scene (true), it stays with the chapter before (false), or the grouping decides (null). */
  mark: (scene: number, start: boolean | null) => Promise<void>
  /** The person's title for the chapter that starts at scene `scene` (not its place in the list, which a regroup
   *  renumbers), or null for the namer's. */
  rename: (scene: number, title: string | null) => Promise<void>
}

/** The chapters' check: the gate waiting for the person, or when they confirmed them; regrouping says so first. */
function ChaptersCheck({ id, run, regrouping }: { id: number; run: Job<WorkflowRun>; regrouping: boolean }) {
  const confirm = useAsyncAction(() =>
    api
      .post('/api/workflows/tape/subjects/{subject}/stages/{stage}/approve', {
        path: { subject: subjectOf(id), stage: 'check_chapters' },
        json: { value: null },
      })
      .then(run.poll),
  )
  // In a div, so the status is as wide as its words, not the list's grid column.
  if (regrouping)
    return (
      <div>
        <Status tone="info">Grouping the chapters again by your marks</Status>
      </div>
    )
  const check = run.status === 'ready' ? checkOf(run.data) : null
  if (check?.state === 'done')
    return (
      <div>
        <Status tone="success">{`Chapters confirmed ${format.ago(check.at)}`}</Status>
      </div>
    )
  if (check?.state !== 'waiting') return null
  return (
    <div className="flex max-w-2xl flex-wrap items-center gap-x-4 gap-y-2">
      <p className="type-body min-w-0 flex-1">
        Are these the tape’s occasions? Start a chapter at a scene, join one to the chapter before or rename it, then confirm them.
      </p>
      <AsyncButton
        variant="default"
        phase={confirm.phase}
        error={confirm.error}
        labels={{ idle: 'Confirm the chapters', pending: 'Confirming…', done: 'Confirmed', error: 'Try again' }}
        onClick={() => void confirm.run()}
      />
    </div>
  )
}

/** A chapter: its name, span and summary, what the namer guessed and why, a doubt where there is one, then its scenes. */
function ChapterItem({
  chapter,
  first,
  onSeek,
  corrections,
  children,
}: {
  chapter: Chapter
  /** Its first scene, where joining it to the chapter before is marked. */
  first: Scene | undefined
  onSeek: (t: number, play?: boolean) => void
  corrections: Corrections
  children: ReactNode
}) {
  const name = chapterName(chapter)
  const n = chapter.name
  const headingId = `chapter-${chapter.index}-heading`
  const unsure = chapter.confidence !== null && chapter.confidence < UNSURE ? chapter.confidence : null
  return (
    <li aria-labelledby={headingId} className="grid min-w-0 gap-4">
      <div className="grid gap-2 border-b border-border pb-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 id={headingId} className="type-title">
            {name}
          </h2>
          <p className="type-body-sm tabular-nums text-muted-foreground">
            {format.runningTime(chapter.start)} to {format.runningTime(chapter.end)}, {format.runningTime(chapter.end - chapter.start)}{' '}
            long, {format.count(chapter.scenes.length, 'scene')}
          </p>
        </div>
        <p className="type-body">{n ? n.summary : 'Not named yet: the name step writes a title and summary for each chapter.'}</p>
        {chapter.title && n && <p className="type-body-sm text-muted-foreground">Your title. The namer called it “{n.title}”.</p>}
        {n && n.inferred.length > 0 && (
          <p className="type-body-sm text-muted-foreground">
            Guessed: {n.inferred.map(g => `${g.guess}, because ${g.because}`).join('; ')}.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 type-body-sm text-muted-foreground">
          <Button size="sm" variant="outline" onClick={() => onSeek(chapter.start, true)}>
            <Play aria-hidden />
            Play chapter {chapter.index + 1}
          </Button>
          <RenameChapter chapter={chapter} rename={corrections.rename} />
          {chapter.index > 0 && first && first.mark === null && (
            <MarkButton
              label={`Join chapter ${chapter.index + 1} to chapter ${chapter.index}`}
              run={() => corrections.mark(first.index, false)}
            />
          )}
          {unsure !== null && <Status tone="warning">{`Start unsure (${format.share(unsure)}): ${chapter.reasons.join(', ')}`}</Status>}
          {n?.mixed && <Status tone="warning">May be more than one occasion</Status>}
        </div>
      </div>
      {children}
    </li>
  )
}

/** A correction's button: its own pending and error, as each scene and chapter has one. */
function MarkButton({ label, run, variant = 'outline' }: { label: string; run: () => Promise<void>; variant?: 'outline' | 'ghost' }) {
  const action = useAsyncAction(run)
  return (
    <AsyncButton
      size="sm"
      variant={variant}
      phase={action.phase}
      error={action.error}
      labels={{ idle: label, pending: 'Saving…', done: 'Saved', error: 'Try again' }}
      onClick={() => void action.run()}
    />
  )
}

/** A chapter's own title: one field in a Popover, and the namer's back when the person has given one. */
function RenameChapter({ chapter, rename }: { chapter: Chapter; rename: Corrections['rename'] }) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(chapterTitle(chapter) ?? '')
  const number = chapter.index + 1
  const save = useAsyncAction(
    async (to: string | null) => {
      await rename(chapter.scenes[0], to)
      setOpen(false)
    },
    { validate: to => ({ title: to === null || to.trim() ? undefined : 'Give the chapter a title of a word or more.' }) },
  )
  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void save.run(title)
  }
  return (
    <Popover
      label={`Title of chapter ${number}`}
      open={open}
      onOpenChange={o => {
        setOpen(o)
        if (o) setTitle(chapterTitle(chapter) ?? '')
      }}
      trigger={
        <Button size="sm" variant="outline">
          Rename chapter {number}
        </Button>
      }
    >
      <form onSubmit={onSubmit} noValidate className="grid gap-3">
        <Field
          label={`Title of chapter ${number}`}
          hint="Yours is shown in place of the namer’s."
          error={save.fieldErrors.title ?? (save.phase === 'error' ? save.error : undefined)}
        >
          <Input value={title} maxLength={120} onChange={e => setTitle(e.target.value)} />
        </Field>
        <div className="flex flex-wrap gap-2">
          <AsyncButton type="submit" size="sm" phase={save.phase} labels={{ idle: 'Save title', pending: 'Saving…', done: 'Saved' }} />
          {chapter.title && chapter.name && (
            <Button size="sm" variant="ghost" onClick={() => void save.run(null)}>
              Use “{chapter.name.title}”
            </Button>
          )}
        </div>
      </form>
    </Popover>
  )
}

// Memoised: only the scene being played gets a `time`, so the playhead re-draws one scene.
const SceneItem = memo(function SceneItem({
  scene,
  time,
  open,
  onOpenChange,
  onSeek,
  onMark,
}: {
  scene: Scene
  time: number | undefined
  open: boolean
  onOpenChange: (open: boolean) => void
  onSeek: (t: number, play?: boolean) => void
  /** Where the person may mark a chapter's start at this scene (stable: the page's one `corrections.mark`). */
  onMark?: Corrections['mark']
}) {
  const name = sceneName(scene)
  const marked = describeMark(scene.mark)
  const d = scene.description
  const faces = describeFaces(scene.faces)
  const headingId = `scene-${scene.index}-heading`
  return (
    <li
      aria-labelledby={headingId}
      aria-current={time !== undefined ? 'time' : undefined}
      className={cn('grid min-w-0 gap-2 border-l-2 pl-3', time !== undefined ? 'border-primary' : 'border-transparent')}
    >
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
        {scene.people?.length ? <span>{describeCast(scene.people)}</span> : faces && <span>{faces}</span>}
        {scene.confidence !== null && scene.confidence < UNSURE && (
          <Status tone="warning">{`Start unsure (${format.share(scene.confidence)})`}</Status>
        )}
        {marked && <Status tone="info">{marked}</Status>}
        {onMark &&
          (marked ? (
            <MarkButton variant="ghost" label={`Undo your mark on ${name.toLowerCase()}`} run={() => onMark(scene.index, null)} />
          ) : (
            <MarkButton variant="ghost" label={`Start a chapter at ${name.toLowerCase()}`} run={() => onMark(scene.index, true)} />
          ))}
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
          {d.who.length > 0 && (
            <div>
              <dt className="type-label">Who does what</dt>
              <dd>
                <ul className="list-disc pl-5">
                  {d.who.map(w => (
                    <li key={`${w.person} ${w.does}`}>
                      {w.person} {w.does}
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
          )}
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
