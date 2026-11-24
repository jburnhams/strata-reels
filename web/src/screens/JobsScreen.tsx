// The one question: "What is the app working on, and did anything fail?" Every step after adding a
// tape runs as a queued job (src/app/jobs.py): the hash, the browser copy, thumbnails, sound, shots,
// keyframes, scenes, speech, faces and descriptions. The finding answers in a sentence (how many are
// running, waiting and failed); the table is strata-react's JobsTable over strata-python's shared
// queue routes (`GET /api/jobs`, cancel and retry), with each job named in the person's words
// ("Find the scenes in christmas-1996.mkv"), not by its kind and domain.
//
// - The state filter is in the URL (?state=failed), so "what failed" can be linked.
// - The list refreshes every few seconds while anything is queued or running, and stops when nothing
//   is: the queue moves on its own, and a page that went stale would say a finished job is running.
import { useQueryClient } from '@tanstack/react-query'
import { DataView, EmptyState, JobsTable, PageHeader, TableSkeleton, ToggleGroup, useLoad, useSearchParam } from 'strata-react'
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

/** The page's finding: what is running, waiting and failed, or that the queue is quiet. */
export function describeQueue(jobs: readonly Pick<Job, 'state'>[]): string {
  const n = (state: Job['state']) => jobs.filter(j => j.state === state).length
  const parts = [
    n('running') && `${n('running')} running`,
    n('queued') && `${n('queued')} waiting`,
    n('failed') && `${n('failed')} failed`,
  ].filter(Boolean)
  return parts.length ? `${parts.join(', ')}.` : 'Nothing to do: every job has finished.'
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
  const jobs = useLoad({
    queryKey: [...keys.jobs, state],
    queryFn: () => api.get('/api/jobs', { query: { state: filter, limit: 200 } }),
    // Moves on its own while work is waiting or running; still once nothing is.
    refetchInterval: q =>
      (q.state.data as JobsPage | undefined)?.items.some(j => j.state === 'queued' || j.state === 'running') ? 3000 : false,
  })
  const sources = useLoad({ queryKey: keys.sources, queryFn: () => api.get('/api/sources') })
  const names = sources.status === 'ready' ? sources.data : []
  const refresh = () => queryClient.invalidateQueries({ queryKey: keys.jobs })
  return (
    <>
      <PageHeader
        title="Jobs"
        scope="The work the app does on each tape after it is added, newest first"
        finding={jobs.status === 'ready' && state === 'all' ? { text: describeQueue(jobs.data.items) } : undefined}
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
          )}
        </DataView>
      </section>
    </>
  )
}
