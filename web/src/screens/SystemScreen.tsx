// The one question: "Is the app healthy, and will its disk hold?" The read is strata-python's
// shared GET /api/system (every strata app answers it alike: version, commit, start time, each
// folder with its disk, each service the app calls) plus this app's own numbers. The finding answers
// with the home disk's state, from the same thresholds the meter draws; then the next questions in
// order: the server, its folders and strata-models, which does every analysis step (strata-react's
// SystemPanel, the page every strata app shares: meters with named thresholds, Up or Down as words),
// what fills the disk (a CompositionBar with its table, this app's own question), and where to go
// next (LinkCards). The read is polled every minute through `usePoll`, which the person can pause
// (`PauseButton`); a poll that fails keeps the last readings on screen and says how old they are
// (the panel's stale notice), so a restarting server does not blank the page.
import {
  CompositionBar,
  crossedThreshold,
  diskThresholds,
  format,
  LinkCard,
  LinkCardList,
  PageHeader,
  PauseButton,
  SystemPanel,
  useLoad,
  usePoll,
} from 'strata-react'
import { api, type Folder, keys, type SystemStatus } from '../api'

/** How often the page reads the server again, unless the person pauses it. */
const EVERY = 60_000

/** The disk a folder is on, in bytes: its size and what is used of it. */
export type Disk = { size: number; used: number; free: number }

/** The home folder's disk (where the settings and the SQLite database live), or undefined when the
 *  server could not read it (the folder is not made yet and nothing above it exists). */
export function homeDisk(s: SystemStatus): Disk | undefined {
  const home = s.folders?.find((f: Folder) => f.name === 'home')
  if (home?.total_bytes == null || home.free_bytes == null) return undefined
  return { size: home.total_bytes, used: home.total_bytes - home.free_bytes, free: home.free_bytes }
}

/** The page's finding: strata-models first when it is down (it does every analysis step, so no
 *  tape is analysed while it is), then the disk the home folder is on. When the latest poll failed,
 *  it says so first and reads the disk as old news. */
export function describeSystem(s: SystemStatus, answering = true): string {
  const disk = describeDisk(s, answering)
  const models = s.services.find(x => x.name === 'strata-models')
  if (!answering || !models || models.ok) return disk
  return `strata-models is not answering, so no tape is analysed until it is. ${disk.replace(/^Everything is running\. /, '')}`
}

/** What the home folder's disk says: its share used against the thresholds the meter draws. */
export function describeDisk(s: SystemStatus, answering = true): string {
  const disk = homeDisk(s)
  if (!disk) return answering ? 'Everything is running. The data disk could not be read.' : 'The server is not answering.'
  const share = disk.used / disk.size
  const crossed = crossedThreshold(share, diskThresholds)
  const left = `${format.bytes(disk.free)} free`
  if (!answering) return `The server is not answering. When last read, the data disk was ${format.percent(share)} full, with ${left}.`
  if (!crossed) return `Everything is running. The data disk is ${format.percent(share)} full, with ${left}.`
  return crossed.tone === 'danger'
    ? `The data disk is ${format.percent(share)} full, with ${left}. Free space before adding more footage.`
    : `The data disk is ${format.percent(share)} full, with ${left}. Nothing needs doing yet.`
}

/** What the home folder's disk holds: this app's data, everything else, and what is free. */
function DiskContents({ s }: { s: SystemStatus }) {
  const disk = homeDisk(s)
  if (!disk) return null
  return (
    <section aria-labelledby="contents" className="grid max-w-xl gap-3">
      <h2 id="contents" className="type-heading">
        What fills the disk
      </h2>
      <CompositionBar
        label={`${format.bytes(disk.size)} in all`}
        parts={[
          { key: 'app', label: 'This app’s data', value: s.home_bytes },
          { key: 'other', label: 'Everything else', value: Math.max(0, disk.used - s.home_bytes) },
        ]}
        total={disk.size}
        restLabel="Free"
        formatValue={v => format.bytes(v)}
      />
    </section>
  )
}

export function SystemScreen() {
  const poll = usePoll({ every: EVERY })
  const load = useLoad({ queryKey: keys.system, queryFn: () => api.get('/api/system'), poll })
  // Readings kept from an earlier poll are still shown, but the server is not answering now.
  const answering = !(load.status === 'ready' && load.stale)

  return (
    <>
      <PageHeader
        title="System"
        scope="This computer and the services the app calls, checked every minute"
        actions={<PauseButton poll={poll} />}
        finding={load.status === 'ready' ? { text: describeSystem(load.data, answering) } : undefined}
      />
      <div className="grid gap-10">
        <SystemPanel
          load={load}
          facts={
            load.status === 'ready'
              ? [{ term: 'Sources', value: <span className="tabular-nums">{format.integer(load.data.sources)}</span> }]
              : []
          }
        />
        {load.status === 'ready' && <DiskContents s={load.data} />}
        <section aria-labelledby="next" className="grid gap-3">
          <h2 id="next" className="type-heading">
            Go to
          </h2>
          <LinkCardList label="Places to go from here">
            <LinkCard to="/settings" title="Settings" description="Choose light or dark." />
            <LinkCard
              href={api.url('/api/docs')}
              title="API reference"
              description="Every endpoint the server answers, with a form to try each one."
            />
            <LinkCard
              href={api.url('/api/health')}
              title="Health check"
              description="The JSON a monitor polls to see that the server is up."
            />
          </LinkCardList>
        </section>
      </div>
    </>
  )
}
