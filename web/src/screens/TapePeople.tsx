// The tape page's People tab. The one question: "Who is on this tape?" (R2's people, src/app/people.py). strata-models
// follows each face through its shot (its `face-tracks` role) and keeps the stills in its face store; the face store
// groups the tracks that look like one person and suggests who, from the faces the family has named before. The
// person answers a group at a time, and the answers are what the scenes' cast and descriptions use ("Tom opens a toy
// car"), so the tape's describe step waits for them (the pipeline's `name_people` gate).
// - One group at a time, the longest on screen first, as the server orders them: a family tape has a few people on
//   screen for most of it and many in passing, so the first few answers name most of the tape. Every track of the
//   group is shown, one still each, the longest first, in a box that scrolls, so the person sees all of it.
// - A group can be more than one person (two cousins the face store cannot tell apart). Choosing some of its faces
//   (a `SelectableGrid`) makes every answer apply to those tracks only: they go, and the rest stay as the group,
//   to be answered next. Nothing chosen means the whole group, the common case.
// - The suggestion, when there is one, is the view's one filled button ("This is Grandma Rose"), since agreeing is the
//   common answer once a few are named; a name of the person's own goes in the field (the filled action when nothing
//   is suggested), or the group is added to someone known already. "Skip" leaves it unnamed (it still counts as on
//   screen, nameless), and "Not faces" says the face finder was wrong (a picture on the wall, a doll), which the face
//   store learns from. Each answer is a `useUndoable` removal: the next group shows at once, and Undo is in reach
//   before the answer goes to the server (no answer is sent until its window closes).
// - "Skip the rest" approves the gate (strata-python's approve route), so the describe step goes on with the faces
//   named so far; a name given later still reaches the scenes, because each answer is one of the cast step's inputs.
// - Faces too small or unsure to name are hidden by the face store's bar and never asked about; the finding says how
//   many, so a person knows why a passer-by is not here.
// - The people named on the tape are listed under it, longest on screen first, each with a face to recognise them by.
import { useQueryClient } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { type FormEvent, useMemo, useState } from 'react'
import {
  AsyncButton,
  Button,
  DataView,
  EmptyState,
  Field,
  format,
  Input,
  type Job,
  NativeSelect,
  SelectableGrid,
  Skeleton,
  Status,
  Thumbnail,
  UndoBar,
  useAsyncAction,
  useLoad,
  useSelection,
  useUndoable,
} from 'strata-react'
import { api, type FaceGroup, type FaceMember, type Known, keys, type People, type Person, subjectOf, type WorkflowRun } from '../api'

/** How often the faces are read again while the tape's job runs (its stages fill them in). */
const FOLLOW = 5000

/** The `name_people` gate as the tab tells it: waiting for the person, or nothing to ask. */
export function namingOf(run: Pick<WorkflowRun, 'stages'>): 'waiting' | 'done' | null {
  const gate = run.stages.find(s => s.name === 'name_people')
  return gate?.state === 'waiting' ? 'waiting' : gate?.state === 'done' ? 'done' : null
}

/** The tab's finding: who is named, what is left to answer, and what was set aside. */
export function describePeople(p: People): string {
  if (p.waiting) return `${p.waiting}.`
  const parts = [p.people.length ? `${format.count(p.people.length, 'person', 'people')} named` : 'Nobody named yet']
  if (p.groups.length) parts.push(`${format.count(p.groups.length, 'group')} of faces to answer`)
  if (p.skipped) parts.push(`${format.count(p.skipped, 'track')} skipped`)
  if (p.not_faces) parts.push(`${format.count(p.not_faces, 'track')} not faces`)
  if (p.hidden) parts.push(`${format.count(p.hidden, 'track')} too small or unsure to ask about`)
  return `${parts.join('; ')}.`
}

/** Nothing at all to show: the face finder found nobody on the tape (as against faces not followed yet, or all answered). */
export const noFaces = (p: People) =>
  p.waiting === null && p.groups.length + p.people.length + p.named + p.skipped + p.not_faces + p.hidden === 0

/** "On screen for 4:12, first at 0:31". */
export const describeGroup = (g: Pick<FaceGroup, 'seconds' | 'first'>) =>
  `On screen for ${format.runningTime(g.seconds)}, first at ${format.runningTime(g.first)}`

/** A person's name, or what they are when the face store has none (a person added without a name). */
export const personName = (p: Pick<Person, 'id' | 'name'>) => p.name ?? `Person ${p.id}`

/** A tile's name for its checkbox: "Face 3, on screen for 1:36 from 20:10". */
export const faceLabel = (m: Pick<FaceMember, 'seconds' | 'start'>, n: number) =>
  `Face ${n}, on screen for ${format.runningTime(m.seconds)} from ${format.runningTime(m.start)}`

/** What a choice of faces does to the answer: the whole group, or the chosen part, the rest left to answer next. */
export function splitNote(chosen: number, of: number): string {
  if (of === 1) return 'One face in this group.'
  if (chosen === 0) return 'Your answer is for the whole group. If some are someone else, choose them to answer for those alone.'
  if (chosen === of) return 'Your answer is for the whole group: every face is chosen.'
  return `Your answer is for the ${format.count(chosen, 'chosen face')}; the other ${format.count(of - chosen, 'face')} stay in the group.`
}

/** What the person said about a group, for the Undo bar. */
export type Answer =
  | { answer: 'name'; name?: string; person?: number; who: string }
  | { answer: 'skip' | 'not-faces'; name?: undefined; person?: undefined }

/** A group less the tracks answered while their Undo window is open: its time on screen and first sight are the rest's,
 * so "Play from" goes to a face still asked about. `tracks` stays whole, as the group's key. */
export function remainOf(g: FaceGroup, isRemoved: (track: string) => boolean): FaceGroup {
  const members = g.members.filter(m => !isRemoved(m.track))
  if (members.length === g.members.length || members.length === 0) return { ...g, members }
  return {
    ...g,
    members,
    seconds: Math.round(members.reduce((n, m) => n + m.seconds, 0) * 10) / 10,
    first: Math.min(...members.map(m => m.start)),
  }
}

/** The Undo bar's sentence for an answer about `n` tracks, the whole group or (`part`) the chosen ones. */
export function answeredNote(said: Answer, n: number, part: boolean): string {
  if (said.answer === 'name') return `Named ${said.who}.`
  const which = part ? format.count(n, 'face') : 'a group of faces'
  return said.answer === 'skip' ? `Skipped ${which}.` : `Marked ${part ? which : 'a group'} as not faces.`
}

const cropUrl = (id: number, face: number) => api.url(`/api/sources/${id}/faces/${face}/crop`)

export function TapePeople({ id, run, onSeek }: { id: number; run: Job<WorkflowRun>; onSeek: (t: number, play?: boolean) => void }) {
  const load = useLoad({
    queryKey: keys.people(id),
    queryFn: () => api.get('/api/sources/{source_id}/people', { path: { source_id: id } }),
    refetchInterval: run.running ? FOLLOW : false,
  })
  return (
    <DataView
      state={load}
      skeleton={
        <div className="grid gap-4" aria-hidden>
          <Skeleton className="h-5 w-2/3" />
          <div className="flex gap-2">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="size-24" />
            ))}
          </div>
          <Skeleton className="h-10 w-full max-w-md" />
        </div>
      }
      empty={<EmptyState title="No faces on this tape">The face finder found nobody to name in any shot of the tape.</EmptyState>}
      isEmpty={p =>
        p.groups.length === 0 && p.people.length === 0 && p.open === 0 && p.waiting === null && !p.hidden && !p.skipped && !p.not_faces
      }
    >
      {p => <PeopleBoard id={id} people={p} run={run} onSeek={onSeek} />}
    </DataView>
  )
}

function PeopleBoard({
  id,
  people,
  run,
  onSeek,
}: {
  id: number
  people: People
  run: Job<WorkflowRun>
  onSeek: (t: number, play?: boolean) => void
}) {
  const queryClient = useQueryClient()
  const undoable = useUndoable()
  const naming = run.status === 'ready' ? namingOf(run.data) : null
  // `tracks` is the whole group, or the part of it the person chose; each track is removed on its own, so the rest of
  // a split group stays on screen while the answer waits out its Undo window.
  const answer = (tracks: string[], part: boolean, said: Answer) => {
    undoable.schedule({
      id: tracks.join(),
      ids: tracks,
      message: answeredNote(said, tracks.length, part),
      failure: part ? 'The faces are back: the answer did not go through.' : 'The group is back: the answer did not go through.',
      commit: () =>
        api
          .post('/api/sources/{source_id}/people', {
            path: { source_id: id },
            json: {
              tracks,
              answer: said.answer,
              name: said.name ?? null,
              person: said.person ?? null,
            },
          })
          .then(p => {
            queryClient.setQueryData(keys.people(id), p)
            void queryClient.invalidateQueries({ queryKey: keys.known })
            run.poll()
          }),
    })
  }
  const skipRest = useAsyncAction(async () => {
    await undoable.flush()
    await api
      .post('/api/workflows/tape/subjects/{subject}/stages/{stage}/approve', {
        path: { subject: subjectOf(id), stage: 'name_people' },
        json: { value: null },
      })
      .then(run.poll)
  })
  const left = people.groups.map(g => remainOf(g, undoable.isRemoved)).filter(g => g.members.length > 0)
  const group = left[0]
  return (
    <div className="grid gap-8">
      <p className="type-body max-w-2xl">{describePeople(people)}</p>
      {naming === 'waiting' && (
        <div className="flex max-w-2xl flex-wrap items-center gap-x-4 gap-y-2">
          <p className="type-body min-w-0 flex-1">
            The scenes are described once the people are named, so a description can say who does what. Answer each group, or skip the rest
            and name them later.
          </p>
          <AsyncButton
            variant="outline"
            phase={skipRest.phase}
            error={skipRest.error}
            labels={{ idle: 'Skip the rest', pending: 'Skipping…', done: 'Skipped the rest', error: 'Try again' }}
            onClick={() => void skipRest.run()}
          />
        </div>
      )}
      {group ? (
        <GroupAnswer
          key={group.tracks[0]}
          id={id}
          group={group}
          left={left.length}
          onAnswer={(tracks, said) => answer(tracks, tracks.length < group.members.length, said)}
          onSeek={onSeek}
        />
      ) : !people.waiting ? (
        <div>
          <Status tone="success">{people.done && people.groups.length === 0 ? 'Everyone is answered' : 'Every group is answered'}</Status>
        </div>
      ) : null}
      <UndoBar undoable={undoable} />
      {people.people.length > 0 && <Named id={id} people={people.people} />}
    </div>
  )
}

function GroupAnswer({
  id,
  group,
  left,
  onAnswer,
  onSeek,
}: {
  id: number
  group: FaceGroup
  /** Groups still to answer, this one included. Its `members` are the tracks not answered yet. */
  left: number
  /** The answer, for the tracks it is about: the chosen ones, or the whole group. */
  onAnswer: (tracks: string[], said: Answer) => void
  onSeek: (t: number, play?: boolean) => void
}) {
  const [name, setName] = useState('')
  const [problem, setProblem] = useState<string>()
  const known = useLoad({ queryKey: keys.known, queryFn: () => api.get('/api/people') })
  const members = group.members
  const selection = useSelection(members, m => m.track)
  const labels = useMemo(() => new Map(members.map((m, i) => [m.track, faceLabel(m, i + 1)])), [members])
  const part = selection.count > 0 && selection.count < members.length
  const answerFor = (said: Answer) => {
    onAnswer(selection.count ? members.filter(m => selection.selected.has(m.track)).map(m => m.track) : members.map(m => m.track), said)
    selection.clear()
    setName('')
  }
  const suggested = group.suggested
  const headingId = `group-${group.tracks[0]}-heading`
  const named = (e: FormEvent) => {
    e.preventDefault()
    const typed = name.trim()
    if (!typed) return setProblem('Type their name, or skip the group.')
    answerFor({ answer: 'name', name: typed, who: typed })
  }
  return (
    <section aria-labelledby={headingId} className="grid max-w-2xl gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 id={headingId} className="type-heading">
          Who is this?
        </h3>
        <p className="type-body-sm tabular-nums text-muted-foreground">{left === 1 ? 'The last group' : `${left} groups left`}</p>
      </div>
      <div className="max-h-80 overflow-y-auto">
        <SelectableGrid
          label="Their faces"
          rows={members}
          rowKey={m => m.track}
          tileSize="sm"
          renderImage={m => (
            <Thumbnail src={m.face === null ? null : cropUrl(id, m.face)} alt="" className="size-full" missing="No picture" />
          )}
          detail={m => <span className="tabular-nums">{format.runningTime(m.seconds)}</span>}
          selection={{ selected: selection.selected, onChange: selection.onChange, rowLabel: m => labels.get(m.track)! }}
        />
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 type-body-sm text-muted-foreground">
        <span aria-live="polite">{splitNote(selection.count, members.length)}</span>
        {selection.count > 0 && (
          <Button size="sm" variant="ghost" onClick={selection.clear}>
            Choose none
          </Button>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 type-body-sm text-muted-foreground">
        <span>
          {describeGroup(group)}, seen {members.length === 1 ? 'once' : `${members.length} times`}
        </span>
        <Button size="sm" variant="outline" onClick={() => onSeek(group.first, true)}>
          <Play aria-hidden />
          Play from {format.runningTime(group.first)}
        </Button>
      </div>
      {suggested && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Button variant="default" onClick={() => answerFor({ answer: 'name', person: suggested.id, who: personName(suggested) })}>
            {part
              ? `${selection.count === 1 ? 'This one is' : `These ${selection.count} are`} ${personName(suggested)}`
              : `This is ${personName(suggested)}`}
          </Button>
          <span className="type-body-sm text-muted-foreground">
            {format.share(suggested.share)} of the nearest faces named before are {personName(suggested)}’s
          </span>
        </div>
      )}
      <form onSubmit={named} noValidate>
        <Field
          label={suggested ? 'Someone else: their name' : 'Their name'}
          error={problem}
          action={
            <Button type="submit" variant={suggested ? 'outline' : 'default'}>
              Name them
            </Button>
          }
        >
          <Input
            value={name}
            onChange={e => {
              setName(e.target.value)
              setProblem(undefined)
            }}
            autoComplete="off"
          />
        </Field>
      </form>
      {/* A read of its own: while it loads, or if it fails, the person sees so (and can try again) instead of an empty
          list that reads as nobody known, which would make a second Grandma. */}
      <DataView
        state={known}
        isEmpty={people => people.every(k => k.id === suggested?.id)}
        skeleton={<Skeleton className="h-10 w-full" />}
        empty={<p className="type-body-sm text-muted-foreground">Nobody else is named on any tape yet.</p>}
      >
        {people => <KnownChoice others={people.filter(k => k.id !== suggested?.id)} onAnswer={answerFor} />}
      </DataView>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => answerFor({ answer: 'skip' })}>
          {part ? `Skip ${format.count(selection.count, 'face')}` : 'Skip'}
        </Button>
        <Button variant="ghost" onClick={() => answerFor({ answer: 'not-faces' })}>
          {part ? `${format.count(selection.count, 'face')} not faces` : 'Not faces'}
        </Button>
      </div>
    </section>
  )
}

/** Adding the group to someone the face store knows already. */
function KnownChoice({ others, onAnswer }: { others: Known[]; onAnswer: (said: Answer) => void }) {
  const [chosen, setChosen] = useState('')
  return (
    <Field
      label="Or someone named before"
      action={
        <Button
          variant="outline"
          disabled={!chosen}
          onClick={() => {
            // enabled only once a listed person is chosen
            const k = others.find(o => String(o.id) === chosen)!
            onAnswer({ answer: 'name', person: k.id, who: personName(k) })
          }}
        >
          Add to them
        </Button>
      }
    >
      <NativeSelect value={chosen} onChange={e => setChosen(e.target.value)}>
        <option value="">Choose a person</option>
        {others.map(k => (
          <option key={k.id} value={k.id}>
            {personName(k)} ({format.count(k.faces, 'face')})
          </option>
        ))}
      </NativeSelect>
    </Field>
  )
}

function Named({ id, people }: { id: number; people: Person[] }) {
  return (
    <section aria-labelledby="named-heading" className="grid max-w-2xl gap-3">
      <h3 id="named-heading" className="type-heading">
        Named on this tape
      </h3>
      <ul className="grid gap-3">
        {people.map(p => (
          <li key={p.id} className="flex items-center gap-3">
            <Thumbnail src={p.face === null ? null : cropUrl(id, p.face)} alt="" className="w-10 rounded-pill" missing="No picture" />
            <div className="min-w-0">
              <p className="type-body">{personName(p)}</p>
              <p className="type-body-sm tabular-nums text-muted-foreground">
                {format.runningTime(p.seconds)} on screen, seen {p.tracks === 1 ? 'once' : `${p.tracks} times`}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
