// Sample data for the mock server (handlers.ts): the same records answer the unit and integration
// tests, every Storybook story and the visual suite's screenshots, so a screen looks the same in
// each. Dates are fixed, so screenshots do not change from day to day.
import type { Source, SystemStatus } from '../api'

const pal = { codec: 'ffv1', width: 720, height: 576, rate: '25/1', field_order: 'tt' }

/** What the server holds in the healthy state: three tape transfers, a phone clip and a file with no picture, newest first, as `GET /api/sources` returns them. */
export const initialSources: Source[] = [
  {
    id: 5,
    name: 'christmas-1996.mkv',
    path: '/home/ada/tapes/christmas-1996.mkv',
    size: 41.7e9,
    duration: 10_812.4,
    video: pal,
    added: '2026-11-09T09:12:00Z',
  },
  {
    id: 4,
    name: 'beach.mp4',
    path: '/home/ada/phone/beach.mp4',
    size: 212e6,
    duration: 94.5,
    video: { codec: 'h264', width: 1920, height: 1080, rate: '30000/1001', field_order: 'progressive' },
    added: '2026-11-08T17:40:00Z',
  },
  {
    id: 3,
    name: 'interview.wav',
    path: '/home/ada/audio/interview.wav',
    size: 58e6,
    duration: 1_204,
    video: null,
    added: '2026-11-07T11:05:00Z',
  },
  {
    id: 2,
    name: 'wedding-1994-side-b.mkv',
    path: '/home/ada/tapes/wedding-1994-side-b.mkv',
    size: 30.2e9,
    duration: 7_830,
    video: pal,
    added: '2026-11-06T20:31:00Z',
  },
  {
    id: 1,
    name: 'wedding-1994-side-a.mkv',
    path: '/home/ada/tapes/wedding-1994-side-a.mkv',
    size: 42.9e9,
    duration: 11_102.2,
    video: pal,
    added: '2026-11-06T19:02:00Z',
  },
]

/** A new source, as the server describes a file it has just read: what `POST /api/sources` answers. */
export function readSource(id: number, path: string): Source {
  const name = path.split('/').pop() ?? path
  return { id, name, path, size: 8.1e9, duration: 2_710, video: pal, added: '2026-11-09T09:30:00Z' }
}

export const GB = 1e9

const home = (total: number, free: number) => [
  { name: 'home', path: '/home/ada/.reels', exists: true, total_bytes: total, free_bytes: free },
]

export const systemStatus: SystemStatus = {
  version: '0.1.0',
  commit: '1427b94c0ffee5ea1d2b3c4d5e6f708192a3b4c5',
  python: '3.13.1',
  platform: 'Linux-6.8.0-x86_64-with-glibc2.39',
  started_at: '2026-11-09T08:00:00Z',
  folders: home(512 * GB, 210.4 * GB),
  services: [],
  sources: initialSources.length,
  home_bytes: 18_432,
}

/** The same server with its disk past the "nearly full" threshold. */
export const systemNearlyFull: SystemStatus = { ...systemStatus, home_bytes: 2.4 * GB, folders: home(512 * GB, 20 * GB) }
