// The one question: "What is the app working on, and did anything fail?" Every step after adding a
// tape runs as a queued job (src/app/jobs.py): the hash, the browser copy, thumbnails, sound, shots,
// keyframes, scenes, speech, faces and descriptions. The finding answers in a sentence (how many are
// running, waiting and failed); the table is strata-react's JobsTable over strata-python's shared
// queue routes (`GET /api/jobs`, cancel and retry), with each job named in the person's words
// ("Find the scenes in christmas-1996.mkv"), not by its kind and domain.
//
// - The state filter is in the URL (?state=failed), so "what failed" can be linked.
// - The finding and the refreshing come from the whole queue's counts (`GET /api/jobs/counts`), not from
//   the page shown, so a running or failed job past the newest 200 is still counted. The list and the
//   counts refresh every few seconds while anything is queued or running, and stop when nothing is: the
//   queue moves on its own, and a page that went stale would say a finished job is running.
// - The list is the newest 200 in the state chosen; when there are more it says so, and the state
//   filter reaches the ones that need a person (failed, waiting). A home queue holds a few tapes' jobs,
//   so paging further back is not worth a control.
import { useQueryClient } from '@tanstack/react-query'
import { DataView, EmptyState, format, JobsTable, PageHeader, TableSkeleton, ToggleGroup, useLoad, useSearchParam } from 'strata-react'
import { api, type Job, type JobsPage, keys, type Source } from '../api'

/** What each kind of job does, as a verb phrase about its source. */
const DOES: Record<string, string> = {
  hash: 'Read all of',
  proxy: 'Make a browser copy of',
  thumbnails: 'Make thumbnails of',
  peaks: 'Draw the sound of',
  shots: 'Find the shots in',
  keyframes: 'Take a picture of each shot in',
  scenes: 'Group the scenes in',
  transcript: 'Write down what is said in',
  faces: 'Find the faces in',
  describe: 'Describe the scenes in',
}

/** A job in the person's words: "Find the shots in christmas-1996.mkv". */
export function describeJob(job: Pick<Job, 'kind' | 'domain'>, sources: readonly Pick<Source, 'id' | 'name'>[]): string {
  const id = /^source:(\d+)$/.exec(job.domain ?? '')?.[1]
  const name = id === undefined ? null : (sources.find(s => s.id === Number(id))?.name ?? `source ${id}`)
  const does = DOES[job.kind]
  if (!does) return name ? `${job.kind} (${name})` : job.kind
  return name ? `${does} ${name}` : `${does} a source`
}

/** The page's finding from the queue's counts by state: what is running, waiting and failed, or that it is quiet. */
export function describeQueue(counts: Readonly<Record<string, number>>): string {
  const n = (state: Job['state']) => counts[state] ?? 0
  const parts = [
    n('running') && `${n('running')} running`,
    n('queued') && `${n('queued')} waiting`,
    n('failed') && `${n('failed')} failed`,
  ].filter(Boolean)
  return parts.length ? `${parts.join(', ')}.` : 'Nothing to do: every job has finished.'
}

/** Whether anything is waiting or running, so the page keeps reading the queue. */
export const isBusy = (counts: Readonly<Record<string, number>> | undefined) => !!counts && (counts.queued ?? 0) + (counts.running ?? 0) > 0

const LIMIT = 200
const EVERY = 3000

/** Under a list cut at the newest LIMIT: how many there are, and how to reach the rest. */
export function describeMore(p: Pick<JobsPage, 'items' | 'total'>): string {
  const of = p.total ? ` of ${format.count(p.total, 'job')}` : ''
  return `Showing the newest ${p.items.length}${of}. Choose Failed or Waiting to find older ones that need you.`
}

const STATES = [
  { value: 'all', label: 'All' },
  { value: 'running', label: 'Running' },
  { value: 'queued', label: 'Waiting' },
  { value: 'failed', label: 'Failed' },
]

export function JobsScreen() {
  const queryClient = useQueryClient()
  const [state] = useSearchParam('state', { default: 'all', parse: raw => STATES.some(s => s.value === raw) })
  const filter = state === 'all' ? undefined : [state as Job['state']]
  // Moves on its own while work is waiting or running; still once nothing is.
  const counts = useLoad({
    queryKey: [...keys.jobs, 'counts'],
    queryFn: () => api.get('/api/jobs/counts'),
    refetchInterval: q => (isBusy(q.state.data as Record<string, number> | undefined) ? EVERY : false),
  })
  const busy = counts.status === 'ready' && isBusy(counts.data)
  const jobs = useLoad({
    queryKey: [...keys.jobs, state],
    queryFn: () => api.get('/api/jobs', { query: { state: filter, limit: LIMIT } }),
    refetchInterval: busy ? EVERY : false,
  })
  const sources = useLoad({ queryKey: keys.sources, queryFn: () => api.get('/api/sources') })
  const names = sources.status === 'ready' ? sources.data : []
  const refresh = () => queryClient.invalidateQueries({ queryKey: keys.jobs })
  return (
    <>
      <PageHeader
        title="Jobs"
        scope="The work the app does on each tape after it is added, newest first"
        finding={counts.status === 'ready' ? { text: describeQueue(counts.data) } : undefined}
      />
      <section aria-labelledby="queue" className="grid gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="queue" className="type-heading">
            Queue
          </h2>
          <ToggleGroup label="Show" param="state" options={STATES} />
        </div>
        <DataView
          state={jobs}
          isEmpty={p => p.items.length === 0}
          skeleton={<TableSkeleton rows={8} columns={4} />}
          empty={
            <EmptyState title={state === 'all' ? 'No jobs yet' : 'None to show'}>
              {state === 'all'
                ? 'Adding a tape on the Sources page queues the work on it, and each step shows here.'
                : 'No job is in this state now. Show all of them to see the rest.'}
            </EmptyState>
          }
        >
          {p => (
            <div className="grid gap-2">
              <JobsTable
                caption="Jobs, newest first"
                jobs={p.items}
                describe={j => describeJob(j, names)}
                onCancel={async j => {
                  await api.post('/api/jobs/{job_id}/cancel', { path: { job_id: Number(j.id) } })
                  await refresh()
                }}
                onRetry={async j => {
                  await api.post('/api/jobs/{job_id}/retry', { path: { job_id: Number(j.id) } })
                  await refresh()
                }}
              />
              {p.next !== null && <p className="type-body-sm text-muted-foreground">{describeMore(p)}</p>}
            </div>
          )}
        </DataView>
      </section>
    </>
  )
}
