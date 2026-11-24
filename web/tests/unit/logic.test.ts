import { readFileSync } from 'node:fs'
import { colorSchemeScript, format } from 'strata-react'
import { describe, expect, it } from 'vitest'
import { initialSources, systemNearlyFull, systemStatus } from '../../src/mocks/fixtures'
import { tapeScenes, tapeTranscript, waitingScenes } from '../../src/mocks/tape'
import { describeJob, describeQueue } from '../../src/screens/JobsScreen'
import { describePicture, describeSources, framesPerSecond } from '../../src/screens/SourcesScreen'
import { describeDisk, homeDisk, ofSize } from '../../src/screens/SystemScreen'
import { aspectOf, describeFaces, describeScenes, sceneName, transcriptGroups } from '../../src/screens/TapeScreen'

describe('the Sources finding and picture', () => {
  it('counts sources and adds up their running time, naming those of unknown length', () => {
    expect(describeSources(initialSources)).toBe(`${format.count(5, 'source')} running ${format.runningTime(31_043.1)} in all.`)
    const untimed = { ...initialSources[0], id: 9, duration: null }
    expect(describeSources([untimed, initialSources[1]])).toBe(
      `${format.count(2, 'source')} running ${format.runningTime(94.5)} in all (${format.count(1, 'source')} of unknown length).`,
    )
    expect(describeSources([untimed])).toBe(`${format.count(1, 'source')} (${format.count(1, 'source')} of unknown length).`)
    expect(describeSources([])).toBe('No footage yet. Add a tape transfer or a clip below.')
  })

  it("reads ffprobe's frame rate and says what a person checks of the picture", () => {
    expect(framesPerSecond('25/1')).toBe(25)
    expect(framesPerSecond('30000/1001')).toBeCloseTo(29.97, 2)
    expect(framesPerSecond('0/0')).toBeNull()
    expect(framesPerSecond(null)).toBeNull()
    const pal = initialSources[0].video
    expect(describePicture(pal)).toBe(`720\u00a0×\u00a0576, ${format.withUnit(25, 'fps')}, interlaced`)
    expect(describePicture(initialSources[1].video)).toBe(`1920\u00a0×\u00a01080, ${format.withUnit(29.97, 'fps')}`)
    expect(describePicture({ codec: null, width: null, height: null, rate: null, field_order: 'unknown' })).toBeNull()
    expect(describePicture(null)).toBeNull()
  })
})

describe('the System finding', () => {
  it("reads the home folder's disk and names the threshold crossed", () => {
    expect(homeDisk(systemStatus)).toEqual({ size: 512e9, used: 512e9 - 210.4e9, free: 210.4e9 })
    expect(
      homeDisk({ ...systemStatus, folders: [{ name: 'home', path: '/x', exists: false, free_bytes: null, total_bytes: null }] }),
    ).toBeUndefined()
    expect(ofSize(301.6e9, 512e9)).toBe(`${format.bytes(301.6e9, 'GB')} of ${format.bytes(512e9, 'GB')}`)
    expect(describeDisk(systemStatus)).toBe(`Everything is running. The data disk is 59% full, with ${format.bytes(210.4e9)} free.`)
    expect(describeDisk(systemNearlyFull)).toMatch(/^The data disk is 96% full, .* Free space before adding more footage\.$/)
    const filling = { ...systemStatus, folders: [{ name: 'home', path: '/x', exists: true, total_bytes: 100, free_bytes: 15 }] }
    expect(describeDisk(systemStatus, false)).toBe(
      `The server is not answering. When last read, the data disk was 59% full, with ${format.bytes(210.4e9)} free.`,
    )
    expect(describeDisk(filling)).toBe(`The data disk is 85% full, with ${format.bytes(15)} free. Nothing needs doing yet.`)
    const unread = { ...systemStatus, folders: [] }
    expect(describeDisk(unread)).toBe('Everything is running. The data disk could not be read.')
    expect(describeDisk(unread, false)).toBe('The server is not answering.')
  })
})

describe('index.html', () => {
  it('inlines the colour-scheme script ThemeProvider expects, so a dark page does not flash light', () => {
    const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
    expect(html).toContain(`<script>${colorSchemeScript()}</script>`)
  })
})

describe('the tape page', () => {
  it('says how many scenes, what is not described, which starts to check and what is to come', () => {
    expect(describeScenes(tapeScenes)).toBe(
      `${format.count(6, 'scene')} over 2:59:48; ${format.count(1, 'scene')} not described yet; 2 starts to check.`,
    )
    expect(describeScenes(waitingScenes(4))).toBe('Grouping the shots into scenes (the keyframes and scenes jobs).')
    const one = { ...tapeScenes, scenes: [{ ...tapeScenes.scenes[0] }], analysis: { ...tapeScenes.analysis, transcript: false } }
    expect(describeScenes(one)).toBe(`${format.count(1, 'scene')} over 6:52; speech still to come.`)
    expect(describeScenes({ ...one, scenes: [], waiting: null })).toBe('No scenes found.')
    const unsure = { ...one, scenes: [{ ...tapeScenes.scenes[4] }] }
    expect(describeScenes(unsure)).toBe(`${format.count(1, 'scene')} over 7:12; not described yet; 1 start to check; speech still to come.`)
  })

  it('counts faces as a floor, and names scenes from one', () => {
    expect(describeFaces(null)).toBeNull()
    expect(describeFaces(0)).toBe('No faces')
    expect(describeFaces(3)).toBe(`Up to ${format.count(3, 'face')}`)
    expect(sceneName({ index: 0 })).toBe('Scene 1')
  })

  it('shows a tape 4:3 whatever its stored pixels, anything else as it is', () => {
    expect(aspectOf(initialSources[0])).toBe('4/3')
    expect(aspectOf(initialSources[1])).toBe('1920/1080')
    expect(aspectOf(initialSources[2])).toBeUndefined()
    expect(aspectOf({ ...initialSources[0], video: { ...initialSources[0].video!, height: 480 } })).toBe('4/3')
    expect(aspectOf({ ...initialSources[0], video: { ...initialSources[0].video!, height: 400 } })).toBe('720/400')
  })

  it('puts each phrase under the scene it starts in, with its words, doubt kept', () => {
    const groups = transcriptGroups(tapeTranscript, tapeScenes.scenes)
    expect(groups.map(g => g.title)).toEqual(['Scene 1', 'Scene 2', 'Scene 4', 'Scene 5', 'Scene 6'])
    expect(groups[0].phrases[0].words[0]).toEqual({ text: 'Is', start: 4, end: 4.4, confidence: 0.93 })
    const hiss = groups[4].phrases[0]
    expect(hiss.words).toEqual([{ text: 'Thank you.', start: 6000, end: 6004, doubtful: true }])
    const odd = transcriptGroups(
      {
        ...tapeTranscript,
        segments: [
          { start: 1, end: 2, text: 'mm', doubtful: true, words: [{ start: 1, end: 2, word: ' mm', probability: 0.9 }] },
          { start: 3, end: 4, text: 'yes', doubtful: false, words: [{ start: 3, end: 4, word: ' yes', probability: null }] },
        ],
      },
      tapeScenes.scenes,
    )
    expect(odd[0].phrases.map(p => p.words[0])).toEqual([
      { text: 'mm', start: 1, end: 2, doubtful: true },
      { text: 'yes', start: 3, end: 4 },
    ])
  })
})

describe('the jobs page', () => {
  it('names a job by what it does to which source', () => {
    expect(describeJob({ kind: 'shots', domain: 'source:5' }, initialSources)).toBe('Find the shots in christmas-1996.mkv')
    expect(describeJob({ kind: 'hash', domain: 'source:77' }, initialSources)).toBe('Read all of source 77')
    expect(describeJob({ kind: 'hash', domain: null }, initialSources)).toBe('Read all of a source')
    expect(describeJob({ kind: 'restore', domain: 'source:5' }, initialSources)).toBe('restore (christmas-1996.mkv)')
    expect(describeJob({ kind: 'restore', domain: null }, [])).toBe('restore')
  })
  it('says what the queue is doing', () => {
    expect(describeQueue([{ state: 'running' }, { state: 'queued' }, { state: 'queued' }, { state: 'done' }])).toBe('1 running, 2 waiting.')
    expect(describeQueue([{ state: 'done' }])).toBe('Nothing to do: every job has finished.')
  })
})
