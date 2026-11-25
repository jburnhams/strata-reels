// The one question: "What footage does the app have to work from?" A source is a file the app knows
// by its content (src/app/sources.py): a tape transfer, a phone clip. The finding answers in a
// sentence (how many, how long in all); then adding one (the one filled button, a one-field form, as
// the server reads paths on its own disk) and the list itself.
//
// - the read is useLoad -> DataView, with a skeleton shaped like the table and an empty state that
//   teaches both ways in (the form here, `./app add` on the command line);
// - the add is useAsyncAction -> AsyncButton in `Field action=`; the server's refusal ("there is no
//   file ...") reaches the field as the error's reason, and a file already known answers 200 with
//   the same record, which the list already shows;
// - the sort is in the URL (?sort=added.desc), so a sorted table can be linked and reloaded;
// - each tape's analysis is a column in a few words (strata-react's `WorkflowSummary` over the shared
//   workflow route's list, `GET /api/workflows/tape/subjects`): analysing and on which step, failed and
//   where, the chapters waiting for the person's check, or done. It is the column that says which tape
//   needs the person, so the finding counts those too. It is a read of its own: a slow one never holds
//   the list (the column shows a dash until it answers), and it is read again while any tape's job runs.
import { useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useMemo, useState } from 'react'
import {
  AsyncButton,
  type Column,
  DataTable,
  DataView,
  EmptyState,
  Field,
  format,
  Input,
  isWorkflowRunning,
  PageHeader,
  summariseWorkflow,
  TableSkeleton,
  TextLink,
  useAsyncAction,
  useLoad,
  WorkflowSummary,
} from 'strata-react'
import { allRuns, api, FINISHED, keys, type Source, type Video, type WorkflowRun } from '../api'

/** Frames a second from ffprobe's "25/1" or "30000/1001", or null when it gave none (or 0/0). */
export function framesPerSecond(rate: string | null): number | null {
  const [num, den] = (rate ?? '').split('/').map(Number)
  return num > 0 && den > 0 ? num / den : null
}

/** "720 × 576, 25 fps, interlaced": what a person checks of a tape's picture, or null when there is no video stream. */
export function describePicture(v: Video | null): string | null {
  if (!v) return null
  const fps = framesPerSecond(v.rate)
  const parts = [
    v.width && v.height ? `${v.width} × ${v.height}` : null,
    fps === null ? null : format.withUnit(Math.round(fps * 100) / 100, 'fps'),
    // ffprobe's field_order: progressive, or tt/bb/tb/bt for the two interlaced orders.
    v.field_order && v.field_order !== 'progressive' && v.field_order !== 'unknown' ? 'interlaced' : null,
  ].filter(Boolean)
  return parts.length ? parts.join(', ') : null
}

/** The page's finding: how many sources and how long they run in all, then which need the person; or what to do when there are none. */
export function describeSources(sources: Source[], runs?: Map<number, WorkflowRun>): string {
  if (sources.length === 0) return 'No footage yet. Add a tape transfer or a clip below.'
  const times = sources.flatMap(s => (s.duration === null ? [] : [s.duration]))
  const length = times.length ? ` running ${format.runningTime(times.reduce((a, b) => a + b, 0))} in all` : ''
  const untimed = sources.length - times.length
  const unknown = untimed ? ` (${format.count(untimed, 'source')} of unknown length)` : ''
  const first = `${format.count(sources.length, 'source')}${length}${unknown}`
  const states = sources.flatMap(s => {
    const run = runs?.get(s.id)
    return run ? [summariseWorkflow(run.stages, { running: isWorkflowRunning(run, FINISHED) }).state] : []
  })
  const count = (state: string) => states.filter(x => x === state).length
  const need = [
    count('waiting') && `${format.count(count('waiting'), 'tape')} to check`,
    count('failed') && `${count('failed')} stopped at a failed step`,
    count('running') && `${count('running')} analysing`,
  ].filter(Boolean)
  return need.length ? `${first}; ${need.join(', ')}.` : `${first}.`
}

/** How often the list reads the runs again while a tape's job runs. */
const FOLLOW = 5000

/** How often to read the runs again: while any tape's job is queued or running, else not at all. */
export function followRuns(runs: Map<number, WorkflowRun> | undefined): number | false {
  return [...(runs?.values() ?? [])].some(r => isWorkflowRunning(r, FINISHED)) ? FOLLOW : false
}

/** The order the analysis column sorts in: what needs the person first. */
const NEEDS = ['waiting', 'failed', 'running', 'stale', 'partial', 'ready', 'done']

function columnsFor(runs: Map<number, WorkflowRun> | null): Column<Source>[] {
  const runOf = (s: Source) => runs?.get(s.id) ?? null
  return [
    {
      key: 'name',
      header: 'Name',
      value: s => s.name,
      // the tape's scenes: its own page
      cell: s => (
        <span title={s.path}>
          <TextLink to={`/sources/${s.id}`}>{s.name}</TextLink>
        </span>
      ),
      truncate: true,
    },
    { key: 'length', header: 'Length', numeric: true, value: s => s.duration, cell: s => format.runningTime(s.duration) },
    { key: 'picture', header: 'Picture', value: s => describePicture(s.video), missing: 'No video' },
    { key: 'size', header: 'Size', numeric: true, value: s => s.size, cell: s => format.bytes(s.size) },
    {
      key: 'analysis',
      header: 'Analysis',
      value: s => {
        const run = runOf(s)
        return run ? NEEDS.indexOf(summariseWorkflow(run.stages, { running: isWorkflowRunning(run, FINISHED) }).state) : null
      },
      // Called only for a row whose `value` is not null (DataTable shows the dash for the rest), so the run is there.
      cell: s => {
        const run = runOf(s) as WorkflowRun
        return <WorkflowSummary stages={run.stages} running={isWorkflowRunning(run, FINISHED)} words={{ running: 'Analysing' }} />
      },
    },
    { key: 'added', header: 'Added', value: s => new Date(s.added), cell: s => format.date(s.added), className: 'whitespace-nowrap' },
  ]
}

export function SourcesScreen() {
  const load = useLoad({ queryKey: keys.sources, queryFn: () => api.get('/api/sources') })
  const runs = useLoad({
    queryKey: keys.runs,
    queryFn: allRuns,
    refetchInterval: q => followRuns(q.state.data),
  })
  const known = runs.status === 'ready' ? runs.data : null
  const columns = useMemo(() => columnsFor(known), [known])
  return (
    <>
      <PageHeader
        title="Sources"
        scope="Footage the app can work from, kept in its database"
        finding={load.status === 'ready' ? { text: describeSources(load.data, known ?? undefined) } : undefined}
      />
      <AddSource />
      <section aria-labelledby="footage" className="grid gap-3">
        <h2 id="footage" className="type-heading">
          Footage
        </h2>
        <DataView
          state={load}
          isEmpty={rows => rows.length === 0}
          skeleton={<TableSkeleton rows={6} columns={6} />}
          empty={
            <EmptyState title="No footage yet">
              Add a file with the form above, or from the command line with <code className="font-mono">./app add /path/to/tape.mkv</code>.
              The file stays where it is; the app reads it there.
            </EmptyState>
          }
        >
          {rows => <DataTable caption="Sources, newest first" columns={columns} rows={rows} rowKey={s => String(s.id)} sortParam="sort" />}
        </DataView>
      </section>
    </>
  )
}

/** Add a file by its path on the server's disk. Its own component so typing does not re-render the table. */
export function AddSource() {
  const queryClient = useQueryClient()
  const [path, setPath] = useState('')
  const add = useAsyncAction(
    async () => {
      await api.post('/api/sources', { json: { path: path.trim() } })
      setPath('')
      await queryClient.invalidateQueries({ queryKey: keys.sources })
      await queryClient.invalidateQueries({ queryKey: keys.system })
    },
    { validate: () => ({ path: path.trim() ? undefined : 'Enter the path of a video file, such as /home/ada/tapes/1994.mkv.' }) },
  )
  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void add.run()
  }
  return (
    <form onSubmit={onSubmit} noValidate className="grid max-w-2xl gap-2">
      <Field
        label="Add footage"
        hint="The path of a video file on this computer. Each file is read once, to learn its length and picture."
        error={add.fieldErrors.path ?? (add.phase === 'error' ? add.error : undefined)}
        action={
          <AsyncButton
            type="submit"
            variant="default"
            phase={add.phase}
            error={add.error}
            labels={{ idle: 'Add', pending: 'Reading…', done: 'Added' }}
          />
        }
      >
        <Input
          value={path}
          onChange={e => {
            setPath(e.target.value)
            add.clearFieldError('path')
          }}
          autoComplete="off"
          spellCheck={false}
          className="font-mono"
        />
      </Field>
    </form>
  )
}
