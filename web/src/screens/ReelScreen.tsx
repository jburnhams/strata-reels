// The one question: "Is this version of the reel the one I want, and can I keep it as a file?" A reel's versions
// (src/app/reels.py) are lists of clips cut from one tape; the planner proposes, the person accepts or rejects, and an
// accepted version can be rendered to a file (src/app/renders.py). The finding says which version is current and what
// waits; then the version shown, played as one film, its clips, and its file.
//
// - The preview is strata-react's EditListPlayer: the clips played from the tape's browser copy, one after another with
//   no gap, so a version is judged as it will be watched. The file is the render; the preview is how to decide.
// - The version shown is in the URL (?version=12), so a proposal can be linked; without it the page shows the current
//   version, or the newest proposal before one is accepted. Every open proposal is listed with Preview.
// - One filled button: Accept on a proposal, Render on the current version (a proposal can be rendered too, outlined).
//   A stale proposal (made on a version that is no longer current) cannot be accepted (the server answers 409), so it
//   says to plan again instead of offering Accept.
// - A render is a job on the queue: its state is read while it waits or runs and the download appears when it is done.
//   Its manifest's SHA-256 is shown beside the file, so a person can check the file is the one the manifest describes.
import { useQueryClient } from '@tanstack/react-query'
import { useMemo, useRef } from 'react'
import { useParams } from 'react-router'
import {
  Alert,
  AsyncButton,
  ButtonLink,
  type Column,
  DataTable,
  DataView,
  type EditClip,
  EditListPlayer,
  type EditListState,
  EmptyState,
  format,
  JobStatus,
  PageHeader,
  RowAction,
  Skeleton,
  Status,
  TextLink,
  type Tone,
  useAsyncAction,
  useLoad,
  useSearchParam,
} from 'strata-react'
import { api, type Clip, keys, type ReelDetail, type Render, type Revision, type Source } from '../api'
import { aspectOf, frameRate } from './TapeScreen'

/** The picture's shape as a number (4/3 for a PAL tape), from the tape's size; 16/9 when it says none. */
export function aspectNumber(source: Source | undefined): number {
  const [w, h] = (source ? (aspectOf(source) ?? '16/9') : '16/9').split('/').map(Number)
  return w / h
}

/** A version's clips as the player takes them: each played from its tape's browser copy, labelled by where it is on the tape. */
export function editOf(clips: readonly Clip[]): EditClip[] {
  return clips.map(c => ({
    id: c.id,
    src: api.url(`/api/sources/${c.source_id}/proxy`),
    start: c.start_seconds,
    end: c.end_seconds,
    label: `From ${format.runningTime(c.start_seconds)} on the tape`,
  }))
}

/** The version a page shows: the one the URL names, else the current one, else the newest proposal. */
export function shownOf(reel: Pick<ReelDetail, 'current' | 'open'>, wanted: string): Revision | null {
  const all = [...(reel.current ? [reel.current] : []), ...reel.open]
  return all.find(r => String(r.id) === wanted) ?? reel.current ?? reel.open[0] ?? null
}

const WORD: Record<Revision['state'], { word: string; tone: Tone }> = {
  proposal: { word: 'Proposed', tone: 'info' },
  stale: { word: 'Out of date', tone: 'warning' },
  current: { word: 'Current', tone: 'success' },
  past: { word: 'Past', tone: 'neutral' },
  rejected: { word: 'Rejected', tone: 'neutral' },
}

/** "Version 12: 2:01 in 14 clips, planned by baseline (seed 0)." */
export function describeRevision(r: Revision): string {
  const by = r.accepted_by ? `, accepted by ${r.accepted_by}` : ''
  return `Version ${r.id}: ${format.runningTime(r.duration_seconds)} in ${format.count(r.clips.length, 'clip')}, planned by ${r.planner.name} (seed ${r.planner.seed})${by}.`
}

/** The page's finding: the current version and what waits for the person. */
export function describeReel(reel: Pick<ReelDetail, 'current' | 'open' | 'proposals'>): string {
  const now = reel.current
    ? `Version ${reel.current.id} is current: ${format.runningTime(reel.current.duration_seconds)} in ${format.count(reel.current.clips.length, 'clip')}.`
    : 'No version is accepted yet.'
  const stale = reel.open.length - reel.proposals
  const waiting = reel.proposals ? ` ${format.count(reel.proposals, 'proposal')} to accept or reject.` : ''
  const old = stale > 0 ? ` ${format.count(stale, 'proposal')} out of date.` : ''
  return `${now}${waiting}${old}`
}

/** What a render's job is doing, in words. */
export function describeRender(r: Pick<Render, 'state' | 'error' | 'bytes'>): string {
  switch (r.state) {
    case 'queued':
      return 'Waiting its turn on the queue: a render reads the whole tape stretch by stretch.'
    case 'running':
      return 'Cutting the clips from the tape and joining them into one file.'
    case 'done':
      return `Rendered to one file${r.bytes === null ? '' : ` of ${format.bytes(r.bytes)}`}.`
    case 'cancelled':
      return 'The render was cancelled.'
    case 'failed':
      return r.error ?? 'The render stopped.'
  }
}

const EVERY = 3000
const going = (renders: readonly Render[] | undefined) => !!renders?.some(r => r.state === 'queued' || r.state === 'running')

export function ReelScreen() {
  const id = Number(useParams().id)
  const load = useLoad({ queryKey: keys.reel(id), queryFn: () => api.get('/api/reels/{reel_id}', { path: { reel_id: id } }) })
  return (
    <>
      <PageHeader
        title={load.status === 'ready' ? load.data.name : 'Reel'}
        scope={load.status === 'ready' ? `A highlight reel of ${load.data.source_name}` : 'A highlight reel of a tape'}
        finding={load.status === 'ready' ? { text: describeReel(load.data) } : undefined}
      />
      <DataView
        state={load}
        isEmpty={r => !r.current && r.open.length === 0}
        skeleton={<ReelSkeleton />}
        empty={
          <EmptyState title="No version to show">
            Every proposal was rejected. Plan another version with <code className="font-mono">./app reel {id}</code>, or make a new reel on
            the <TextLink to="/reels">Reels page</TextLink>.
          </EmptyState>
        }
      >
        {r => <ReelBody reel={r} />}
      </DataView>
    </>
  )
}

function ReelSkeleton() {
  return (
    <div aria-hidden className="grid gap-4 xl:grid-cols-2">
      <Skeleton className="aspect-4/3 w-full max-w-2xl" />
      <div className="grid content-start gap-2">
        {[0, 1, 2, 3, 4].map(i => (
          <Skeleton key={i} className="h-(--control-h) w-full" />
        ))}
      </div>
    </div>
  )
}

function ReelBody({ reel }: { reel: ReelDetail }) {
  const [wanted, setWanted] = useSearchParam<string>('version', { default: '', replace: false })
  const shown = shownOf(reel, wanted) as Revision
  const sources = useLoad({ queryKey: keys.sources, queryFn: () => api.get('/api/sources') })
  const source = sources.status === 'ready' ? sources.data.find(s => s.id === reel.source_id) : undefined
  const player = useRef<EditListState | null>(null)
  const edit = useMemo(() => editOf(shown.clips), [shown.clips])
  const versions = useMemo(() => [...(reel.current ? [reel.current] : []), ...reel.open], [reel.current, reel.open])
  // Columns and rows kept from render to render: new ones remount the cells, and a press that re-renders the table (it
  // moves focus to the row) would lose its button between the pointer going down and up.
  const versionCols = useMemo(() => versionColumns(shown.id, v => setWanted(String(v))), [shown.id, setWanted])
  const clipCols = useMemo(
    () =>
      clipColumns(at => {
        player.current?.seek(at)
        void player.current?.play()
      }),
    [],
  )
  const clipRows = useMemo(() => shown.clips.map((c, index) => ({ ...c, index })), [shown.clips])
  return (
    <div className="grid gap-8 xl:grid-cols-2 xl:items-start">
      <section aria-label="Preview" className="grid min-w-0 gap-4 xl:sticky xl:top-0 xl:py-2">
        <EditListPlayer
          label={`Preview of version ${shown.id}`}
          clips={edit}
          aspect={aspectNumber(source)}
          fps={source ? frameRate(source) : undefined}
          playerRef={player}
          empty={
            <EmptyState title="This version has no clips">
              Plan another version: the planner found nothing to cut at this length.
            </EmptyState>
          }
          className="w-full max-w-2xl"
        />
        <VersionActions reel={reel} shown={shown} onShow={v => setWanted(String(v))} />
        <RenderPanel reel={reel} shown={shown} />
      </section>
      <div className="grid min-w-0 gap-8">
        {versions.length > 1 && (
          <section aria-labelledby="versions" className="grid gap-3">
            <h2 id="versions" className="type-heading">
              Versions
            </h2>
            <DataTable
              caption="The current version and the open proposals"
              columns={versionCols}
              rows={versions}
              rowKey={v => String(v.id)}
            />
          </section>
        )}
        <section aria-labelledby="clips" className="grid gap-3">
          <h2 id="clips" className="type-heading">
            Clips of version {shown.id}
          </h2>
          <DataTable caption={`Clips of version ${shown.id}, in play order`} columns={clipCols} rows={clipRows} rowKey={c => c.id} />
        </section>
      </div>
    </div>
  )
}

function versionColumns(shown: number, onShow: (id: number) => void): Column<Revision>[] {
  return [
    { key: 'id', header: 'Version', numeric: true, value: v => v.id },
    {
      key: 'state',
      header: 'State',
      value: v => WORD[v.state].word,
      cell: v => <Status tone={WORD[v.state].tone}>{WORD[v.state].word}</Status>,
    },
    { key: 'length', header: 'Length', numeric: true, value: v => v.duration_seconds, cell: v => format.runningTime(v.duration_seconds) },
    { key: 'clips', header: 'Clips', numeric: true, value: v => v.clips.length },
    { key: 'made', header: 'Made', value: v => new Date(v.created), cell: v => format.dateTime(v.created), className: 'whitespace-nowrap' },
    {
      key: 'preview',
      header: 'Preview',
      hideHeader: true,
      cell: v => (v.id === shown ? 'Shown' : <RowAction label="Preview" subject={`version ${v.id}`} onClick={() => onShow(v.id)} />),
    },
  ]
}

type ClipRow = Clip & { index: number }

function clipColumns(onPlay: (at: number) => void): Column<ClipRow>[] {
  return [
    {
      key: 'n',
      header: 'Clip',
      numeric: true,
      value: c => c.index + 1,
    },
    { key: 'at', header: 'In the reel', numeric: true, value: c => c.at_seconds, cell: c => format.runningTime(c.at_seconds) },
    { key: 'tape', header: 'On the tape', numeric: true, value: c => c.start_seconds, cell: c => format.runningTime(c.start_seconds) },
    {
      key: 'length',
      header: 'Length',
      numeric: true,
      value: c => c.end_seconds - c.start_seconds,
      cell: c => format.withUnit(Math.round((c.end_seconds - c.start_seconds) * 10) / 10, 's'),
    },
    { key: 'why', header: 'Why', value: c => (c.reasons.length ? c.reasons.join('; ') : null), truncate: true },
    {
      key: 'play',
      header: 'Play',
      hideHeader: true,
      cell: c => <RowAction label="Play" subject={`clip ${c.index + 1}`} onClick={() => onPlay(c.at_seconds)} />,
    },
  ]
}

/** What the person does with the version shown: accept or reject a proposal, or ask the planner for another. */
function VersionActions({ reel, shown, onShow }: { reel: ReelDetail; shown: Revision; onShow: (id: number) => void }) {
  const queryClient = useQueryClient()
  const changed = async (r: Revision) => {
    await queryClient.invalidateQueries({ queryKey: keys.reel(reel.id) })
    await queryClient.invalidateQueries({ queryKey: keys.reels })
    onShow(r.id)
  }
  const path = { reel_id: reel.id, revision_id: shown.id }
  const accept = useAsyncAction(() => api.post('/api/reels/{reel_id}/revisions/{revision_id}/accept', { path }).then(changed))
  const reject = useAsyncAction(() => api.post('/api/reels/{reel_id}/revisions/{revision_id}/reject', { path }).then(changed))
  // Another seed is another version of the same reel; the newest seed shown plus one is one not tried yet.
  const seed = Math.max(0, ...[reel.current, ...reel.open].map(r => r?.planner.seed ?? 0)) + 1
  const plan = useAsyncAction(() => api.post('/api/reels/{reel_id}/plan', { path: { reel_id: reel.id }, json: { seed } }).then(changed))
  const failed = [accept, reject, plan].find(a => a.phase === 'error')
  const open = shown.state === 'proposal' || shown.state === 'stale'
  return (
    <div className="grid max-w-2xl gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Status tone={WORD[shown.state].tone}>{WORD[shown.state].word}</Status>
        <p className="type-body-sm min-w-0 flex-1 text-muted-foreground">{describeRevision(shown)}</p>
      </div>
      {shown.state === 'stale' && (
        <Alert tone="warning">
          This proposal was made on a version that is no longer current, so it cannot be accepted. Plan another version.
        </Alert>
      )}
      {shown.conflicts.length > 0 && (
        <Alert tone="warning" title="What the plan could not keep">
          <ul className="list-disc pl-5">
            {shown.conflicts.map(c => (
              <li key={`${c.override ?? 'length'}-${c.message}`}>{c.message}</li>
            ))}
          </ul>
        </Alert>
      )}
      <div className="flex flex-wrap gap-2">
        {shown.state === 'proposal' && (
          <AsyncButton
            variant="default"
            phase={accept.phase}
            error={accept.error}
            labels={{ idle: 'Accept this version', pending: 'Accepting…', done: 'Accepted' }}
            onClick={() => void accept.run()}
          />
        )}
        {open && (
          <AsyncButton
            phase={reject.phase}
            error={reject.error}
            labels={{ idle: 'Reject', pending: 'Rejecting…', done: 'Rejected' }}
            onClick={() => void reject.run()}
          />
        )}
        <AsyncButton
          phase={plan.phase}
          error={plan.error}
          labels={{ idle: 'Plan another version', pending: 'Planning…', done: 'Planned' }}
          onClick={() => void plan.run()}
        />
      </div>
      {failed && (
        <Alert tone="danger" announce>
          {failed.error}
        </Alert>
      )}
    </div>
  )
}

/** The version shown as a file: Render, its job while it is made, then the download and the file's SHA-256. */
function RenderPanel({ reel, shown }: { reel: ReelDetail; shown: Revision }) {
  const queryClient = useQueryClient()
  const renders = useLoad({
    queryKey: keys.renders(reel.id),
    queryFn: () => api.get('/api/reels/{reel_id}/renders', { path: { reel_id: reel.id } }),
    refetchInterval: q => (going(q.state.data) ? EVERY : false),
  })
  const latest = renders.status === 'ready' ? renders.data.find(r => r.revision_id === shown.id) : undefined
  const render = useAsyncAction(async () => {
    await api.post('/api/reels/{reel_id}/revisions/{revision_id}/render', {
      path: { reel_id: reel.id, revision_id: shown.id },
    })
    await queryClient.invalidateQueries({ queryKey: keys.renders(reel.id) })
  })
  const again = latest?.state === 'failed' || latest?.state === 'cancelled'
  return (
    <section aria-labelledby="file" className="grid max-w-2xl gap-3">
      <h2 id="file" className="type-heading">
        File
      </h2>
      {latest && latest.state !== 'done' && (
        <JobStatus
          label={`Render of version ${shown.id}`}
          running={latest.state === 'queued' || latest.state === 'running'}
          words={{ running: latest.state === 'queued' ? 'Waiting' : 'Rendering', finished: 'Rendered', failed: 'Failed' }}
          summary={describeRender(latest)}
          failed={latest.state === 'failed'}
          cancelled={latest.state === 'cancelled'}
        />
      )}
      {latest?.state === 'done' && latest.url ? (
        <div className="grid gap-2">
          <div className="flex flex-wrap items-center gap-3">
            <Status tone="success">Rendered</Status>
            <p className="type-body-sm text-muted-foreground">{describeRender(latest)}</p>
          </div>
          <div>
            <ButtonLink href={api.url(latest.url)} download variant={shown.state === 'current' ? 'default' : 'outline'}>
              Download the reel
            </ButtonLink>
          </div>
          {latest.sha256 && (
            <p className="type-caption break-all text-muted-foreground">
              SHA-256 <span className="font-mono">{latest.sha256}</span>
            </p>
          )}
        </div>
      ) : (
        !(latest && latest.state !== 'failed' && latest.state !== 'cancelled') && (
          <div className="grid gap-2">
            <p className="type-body-sm text-muted-foreground">
              Rendering cuts these clips from the tape itself, frame for frame, into one MP4 file with a manifest of what made it.
            </p>
            <div>
              <AsyncButton
                variant={shown.state === 'current' ? 'default' : 'outline'}
                phase={render.phase}
                error={render.error}
                labels={{ idle: again ? 'Render again' : 'Render this version', pending: 'Queuing…', done: 'Queued' }}
                onClick={() => void render.run()}
              />
            </div>
            {render.phase === 'error' && (
              <Alert tone="danger" announce>
                {render.error}
              </Alert>
            )}
          </div>
        )
      )}
    </section>
  )
}
