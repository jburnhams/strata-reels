// The one question: "Which reels are there, and which wait for me?" A reel is a highlight reel of one tape
// (src/app/reels.py): a length the person asked for and a run of revisions, each a list of clips the planner cut from
// the tape's scenes. The finding counts the reels and the ones whose newest version waits to be accepted; then making
// one (the one filled button: a tape and a length) and the list.
//
// - the make is useAsyncAction -> AsyncButton; the server's refusal (a tape whose scenes are not grouped yet, a length
//   of 0) reaches the form as its reason, and a made reel opens on its own page, where its first version plays;
// - the length is typed as a clock ("2:00") or seconds ("90"), as `./app reel --target` takes it;
// - the sort is in the URL, as on the Sources page.
import { useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  AsyncButton,
  type Column,
  DataTable,
  DataView,
  EmptyState,
  Field,
  format,
  Input,
  NativeSelect,
  PageHeader,
  TableSkeleton,
  TextLink,
  useAsyncAction,
  useLoad,
} from 'strata-react'
import { api, keys, type Reel } from '../api'

/** Seconds from "2:00", "1:02:30" or "90", or null for anything else (and for 0). */
export function parseLength(text: string): number | null {
  const t = text.trim()
  if (!/^\d+(:\d{1,2}){0,2}$/.test(t)) return null
  const seconds = t.split(':').reduce((sum, part) => sum * 60 + Number(part), 0)
  return seconds > 0 ? seconds : null
}

/** The page's finding: how many reels, and how many have a version waiting for the person. */
export function describeReels(reels: readonly Pick<Reel, 'proposals'>[]): string {
  if (!reels.length) return 'No reels yet.'
  const waiting = reels.filter(r => r.proposals > 0).length
  const all = `${format.count(reels.length, 'reel')}.`
  return waiting ? `${all} ${format.count(waiting, 'reel')} ${waiting === 1 ? 'has a version' : 'have versions'} for you to accept.` : all
}

const columns: Column<Reel>[] = [
  { key: 'name', header: 'Name', value: r => r.name, cell: r => <TextLink to={`/reels/${r.id}`}>{r.name}</TextLink>, truncate: true },
  { key: 'tape', header: 'Tape', value: r => r.source_name, truncate: true },
  {
    key: 'length',
    header: 'Length',
    numeric: true,
    value: r => r.duration_seconds,
    cell: r => format.runningTime(r.duration_seconds),
    missing: 'Not chosen',
  },
  { key: 'target', header: 'Asked for', numeric: true, value: r => r.target_seconds, cell: r => format.runningTime(r.target_seconds) },
  { key: 'clips', header: 'Clips', numeric: true, value: r => r.clips },
  { key: 'proposals', header: 'To accept', numeric: true, value: r => r.proposals, cell: r => (r.proposals ? r.proposals : 'None') },
  { key: 'created', header: 'Made', value: r => new Date(r.created), cell: r => format.date(r.created), className: 'whitespace-nowrap' },
]

export function ReelsScreen() {
  const load = useLoad({ queryKey: keys.reels, queryFn: () => api.get('/api/reels') })
  return (
    <>
      <PageHeader
        title="Reels"
        scope="Highlight reels cut from a tape's scenes, each a run of versions"
        finding={load.status === 'ready' ? { text: describeReels(load.data) } : undefined}
      />
      <MakeReel />
      <section aria-labelledby="reels" className="grid gap-3">
        <h2 id="reels" className="type-heading">
          All reels
        </h2>
        <DataView
          state={load}
          isEmpty={rows => rows.length === 0}
          skeleton={<TableSkeleton rows={4} columns={7} />}
          empty={
            <EmptyState title="No reels yet">
              Choose a tape and a length above, or from the command line run <code className="font-mono">./app reel 5 --target 2:00</code>.
              A tape can be cut once its scenes are grouped.
            </EmptyState>
          }
        >
          {rows => (
            <DataTable
              caption="Reels, newest first"
              columns={columns}
              rows={[...rows].reverse()}
              rowKey={r => String(r.id)}
              sortParam="sort"
            />
          )}
        </DataView>
      </section>
    </>
  )
}

/** A tape and a length: the planner's first version, opened on the reel's page. */
export function MakeReel() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const sources = useLoad({ queryKey: keys.sources, queryFn: () => api.get('/api/sources') })
  const tapes = sources.status === 'ready' ? sources.data.filter(s => s.video) : []
  const [chosen, setChosen] = useState('')
  const [length, setLength] = useState('2:00')
  const tape = chosen || String(tapes[0]?.id ?? '')
  const make = useAsyncAction(
    async () => {
      const reel = await api.post('/api/reels', { json: { source_id: Number(tape), target_seconds: parseLength(length) as number } })
      await queryClient.invalidateQueries({ queryKey: keys.reels })
      await navigate(`/reels/${reel.id}`)
    },
    {
      validate: () => ({
        tape: tape ? undefined : 'Add a tape on the Sources page first: a reel is cut from one.',
        length: parseLength(length) ? undefined : 'Enter a length as minutes and seconds, such as 2:00, or as seconds.',
      }),
    },
  )
  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void make.run()
  }
  return (
    <form onSubmit={onSubmit} noValidate aria-label="Make a reel" className="flex max-w-3xl flex-wrap items-start gap-3">
      {/* A refusal is about the tape (its scenes not grouped yet), so the server's sentence goes under it. */}
      <Field label="Tape" error={make.fieldErrors.tape ?? (make.phase === 'error' ? make.error : undefined)} className="min-w-64 flex-1">
        <NativeSelect
          value={tape}
          onChange={e => {
            setChosen(e.target.value)
            make.clearFieldError('tape')
          }}
        >
          {tapes.map(s => (
            <option key={s.id} value={String(s.id)}>
              {s.name}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field label="Length" error={make.fieldErrors.length} className="w-36">
        <Input
          value={length}
          onChange={e => {
            setLength(e.target.value)
            make.clearFieldError('length')
          }}
          inputMode="numeric"
          autoComplete="off"
          className="tabular-nums"
        />
      </Field>
      {/* Level with the controls: the labels above them are one line. */}
      <div className="grid gap-2">
        <span aria-hidden className="type-label invisible">
          Make
        </span>
        <AsyncButton
          type="submit"
          variant="default"
          phase={make.phase}
          error={make.error}
          labels={{ idle: 'Make a reel', pending: 'Planning…', done: 'Made' }}
        />
      </div>
    </form>
  )
}
