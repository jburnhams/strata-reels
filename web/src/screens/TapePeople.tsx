// The tape page's People tab. The one question: "Who is on this tape?" (R2's people, src/app/people.py). strata-models
// follows each face through its shot (its `face-tracks` role) and keeps the stills in its face store; the face store
// groups the tracks that look like one person and suggests who, from the faces the family has named before. The
// person answers a group at a time, and the answers are what the scenes' cast and descriptions use ("Tom opens a toy
// car"), so the tape's describe step waits for them (the pipeline's `name_people` gate).
// - One group at a time, the longest on screen first, as the server orders them: a family tape has a few people on
//   screen for most of it and many in passing, so the first few answers name most of the tape. Its crops are the
//   face store's best stills (sharpest, most frontal), so the person recognises them at a glance.
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
import { type FormEvent, useState } from 'react'
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
  Skeleton,
  Status,
  Thumbnail,
  UndoBar,
  useAsyncAction,
  useLoad,
  useUndoable,
} from 'strata-react'
import { api, type FaceGroup, type Known, keys, type People, type Person, subjectOf, type WorkflowRun } from '../api'

/** How often the faces are read again while the tape's job runs (its stages fill them in). */
const FOLLOW = 5000
/** Crops shown of a group: enough to recognise someone from a few angles. */
const CROPS = 6

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

/** What the person said about a group, for the Undo bar. */
type Answer =
  | { answer: 'name'; name?: string; person?: number; who: string }
  | { answer: 'skip' | 'not-faces'; name?: undefined; person?: undefined }

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
  const answer = (group: FaceGroup, said: Answer) => {
    const key = group.tracks.join()
    const what =
      said.answer === 'name' ? `Named ${said.who}` : said.answer === 'skip' ? 'Skipped a group of faces' : 'Marked a group as not faces'
    undoable.schedule({
      id: key,
      message: `${what}.`,
      failure: 'The group is back: the answer did not go through.',
      commit: () =>
        api
          .post('/api/sources/{source_id}/people', {
            path: { source_id: id },
            json: {
              tracks: group.tracks,
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
  const left = people.groups.filter(g => !undoable.isRemoved(g.tracks.join()))
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
          key={group.tracks.join()}
          id={id}
          group={group}
          left={left.length}
          onAnswer={said => answer(group, said)}
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
  /** Groups still to answer, this one included. */
  left: number
  onAnswer: (said: Answer) => void
  onSeek: (t: number, play?: boolean) => void
}) {
  const [name, setName] = useState('')
  const [problem, setProblem] = useState<string>()
  const known = useLoad({ queryKey: keys.known, queryFn: () => api.get('/api/people') })
  const suggested = group.suggested
  const headingId = `group-${group.tracks[0]}-heading`
  const named = (e: FormEvent) => {
    e.preventDefault()
    const typed = name.trim()
    if (!typed) return setProblem('Type their name, or skip the group.')
    onAnswer({ answer: 'name', name: typed, who: typed })
  }
  return (
    <section aria-labelledby={headingId} className="grid max-w-2xl gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 id={headingId} className="type-heading">
          Who is this?
        </h3>
        <p className="type-body-sm tabular-nums text-muted-foreground">{left === 1 ? 'The last group' : `${left} groups left`}</p>
      </div>
      <ul className="flex flex-wrap gap-2" aria-label="Their faces">
        {group.faces.slice(0, CROPS).map((f, i) => (
          <li key={f}>
            <Thumbnail src={cropUrl(id, f)} alt={`Face ${i + 1} of the group`} className="w-24" missing="No picture" />
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 type-body-sm text-muted-foreground">
        <span>
          {describeGroup(group)}, seen {group.tracks.length === 1 ? 'once' : `${group.tracks.length} times`}
        </span>
        <Button size="sm" variant="outline" onClick={() => onSeek(group.first, true)}>
          <Play aria-hidden />
          Play from {format.runningTime(group.first)}
        </Button>
      </div>
      {suggested && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Button variant="default" onClick={() => onAnswer({ answer: 'name', person: suggested.id, who: personName(suggested) })}>
            This is {personName(suggested)}
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
        {people => <KnownChoice others={people.filter(k => k.id !== suggested?.id)} onAnswer={onAnswer} />}
      </DataView>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => onAnswer({ answer: 'skip' })}>
          Skip
        </Button>
        <Button variant="ghost" onClick={() => onAnswer({ answer: 'not-faces' })}>
          Not faces
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
