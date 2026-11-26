import { readFileSync } from 'node:fs'
import { colorSchemeScript, format } from 'strata-react'
import { describe, expect, it } from 'vitest'
import { initialSources, systemNearlyFull, systemStatus } from '../../src/mocks/fixtures'
import { otherRuns, tapeRunChecked, tapeRunDone, tapeRunPartWay, tapeScenes, tapeTranscript, waitingScenes } from '../../src/mocks/tape'
import { describeJob, describeMore, describeQueue, isBusy } from '../../src/screens/JobsScreen'
import { describePicture, describeSources, followRuns, framesPerSecond } from '../../src/screens/SourcesScreen'
import { describeDisk, homeDisk, ofSize } from '../../src/screens/SystemScreen'
import {
  aspectOf,
  chapterContents,
  chapterName,
  checkOf,
  describeFaces,
  describeJobNow,
  describeMark,
  describeScenes,
  frameRate,
  sceneName,
  seekMarks,
  soundMarks,
  transcriptGroups,
} from '../../src/screens/TapeScreen'

describe('the Sources finding and picture', () => {
  it('names the tapes that need the person after the footage', () => {
    const runs = new Map([[5, tapeRunDone], ...Object.entries(otherRuns).map(([id, r]) => [Number(id), r] as const)])
    expect(describeSources(initialSources, runs)).toBe(
      `${format.count(5, 'source')} running ${format.runningTime(31_043.1)} in all; ${format.count(1, 'tape')} to check, 1 stopped at a failed step, 1 analysing.`,
    )
    expect(describeSources(initialSources, new Map([[1, otherRuns[1]]]))).toMatch(/in all\.$/)
    // The list is read again only while a tape's job runs.
    expect(followRuns(new Map([[4, otherRuns[4]]]))).toBe(5000)
    expect(followRuns(new Map([[1, otherRuns[1]]]))).toBe(false)
    expect(followRuns(undefined)).toBe(false)
  })

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
      `${format.count(3, 'chapter')} of ${format.count(6, 'scene')} over 2:59:48; ${format.count(1, 'scene')} not described yet; 2 starts to check.`,
    )
    expect(describeScenes(waitingScenes(4))).toBe('Grouping the shots into scenes (the keyframes and scenes steps).')
    const one = {
      ...tapeScenes,
      chapters: [],
      scenes: [{ ...tapeScenes.scenes[0] }],
      analysis: { ...tapeScenes.analysis, transcript: false },
    }
    expect(describeScenes(one)).toBe(`${format.count(1, 'scene')} over 6:52; speech still to come.`)
    expect(describeScenes({ ...one, scenes: [], waiting: null })).toBe('No scenes found.')
    const unsure = { ...one, scenes: [{ ...tapeScenes.scenes[4] }] }
    expect(describeScenes(unsure)).toBe(`${format.count(1, 'scene')} over 7:12; not described yet; 1 start to check; speech still to come.`)
  })

  it('names chapters from one, and marks the sound at chapters once there are any', () => {
    const [first] = tapeScenes.chapters!
    expect(chapterName(first)).toBe('Chapter 1: Christmas morning')
    expect(chapterName({ index: 1, name: null, title: null })).toBe('Chapter 2')
    expect(chapterName({ ...first, title: 'Presents at home' })).toBe('Chapter 1: Presents at home') // the person's wins
    expect(chapterName({ index: 1, name: null, title: 'Tea' })).toBe('Chapter 2: Tea')
    expect(soundMarks(tapeScenes.scenes, tapeScenes.chapters).map(m => m.label)).toEqual([
      'Chapter 1: Christmas morning',
      'Chapter 2: Christmas dinner',
      'Chapter 3: The evening in front of the television',
    ])
    expect(soundMarks(tapeScenes.scenes, null)).toHaveLength(6)
    // The seek bar's marks leave out the first chapter, which starts where the bar does (the contents keep every one), and
    // add each scene that does not start a chapter, as a scene.
    expect(seekMarks(tapeScenes.scenes, tapeScenes.chapters).map(m => [m.label, m.kind])).toEqual([
      ['Scene 2', 'scene'],
      ['Scene 3', 'scene'],
      ['Chapter 2: Christmas dinner', 'chapter'],
      ['Scene 5', 'scene'],
      ['Chapter 3: The evening in front of the television', 'chapter'],
    ])
    expect(seekMarks(tapeScenes.scenes, null).every(m => m.kind === 'scene')).toBe(true)
    expect(seekMarks([], null)).toEqual([])
    expect(chapterContents(tapeScenes.chapters).map(c => [c.title, c.detail])).toEqual([
      ['Chapter 1: Christmas morning', format.count(3, 'scene')],
      ['Chapter 2: Christmas dinner', format.count(2, 'scene')],
      ['Chapter 3: The evening in front of the television', format.count(1, 'scene')],
    ])
    expect(describeScenes({ ...tapeScenes, chapters: null })).toMatch(/; chapters still to come\.$/)
    expect(describeScenes({ ...tapeScenes, regrouping: true })).toMatch(/; grouping the chapters again by your marks\.$/)
  })

  it('says what a mark on a scene says, and where the chapters’ check stands', () => {
    expect(describeMark(true)).toBe('You started a chapter here')
    expect(describeMark(false)).toBe('You kept this with the chapter before')
    expect(describeMark(null)).toBeNull()
    expect(checkOf(tapeRunDone)).toEqual({ state: 'waiting' })
    expect(checkOf(tapeRunChecked)).toEqual({ state: 'done', at: tapeRunChecked.stages.at(-1)!.last!.at })
    expect(checkOf(tapeRunPartWay)).toBeNull() // still blocked behind the steps
  })

  it('says what the tape’s job is doing now', () => {
    const progress = { done: 4, total: 10, text: 'Find the faces', stage: 'faces', at: null }
    expect(describeJobNow({ state: 'running', attempts: 1, progress })).toBe('Find the faces')
    expect(describeJobNow({ state: 'running', attempts: 1, progress: null })).toBe('Starting')
    expect(describeJobNow({ state: 'queued', attempts: 0, progress: null })).toBe('Waiting for its turn')
    expect(describeJobNow({ state: 'queued', attempts: 2, progress: null })).toBe('Trying the failed step again shortly')
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
    expect(frameRate(initialSources[0])).toBe(25)
    expect(frameRate(initialSources[2])).toBeUndefined()
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
    expect(describeJob({ kind: 'workflow:tape', domain: 'tape:source:5' }, initialSources)).toBe('Analyse christmas-1996.mkv')
    expect(describeJob({ kind: 'workflow:tape', domain: 'tape:source:77' }, initialSources)).toBe('Analyse source 77')
    expect(describeJob({ kind: 'workflow:tape', domain: null }, initialSources)).toBe('Analyse a source')
    expect(describeJob({ kind: 'restore', domain: 'tape:source:5' }, initialSources)).toBe('restore (christmas-1996.mkv)')
    expect(describeJob({ kind: 'restore', domain: null }, [])).toBe('restore')
  })
  it('says what the queue is doing', () => {
    expect(describeQueue({ running: 1, queued: 2, done: 7 })).toBe('1 running, 2 waiting.')
    expect(describeQueue({ done: 1 })).toBe('Nothing to do: every job has finished.')
    expect(describeQueue({})).toBe('Nothing to do: every job has finished.')
    expect([isBusy(undefined), isBusy({ done: 3, failed: 1 }), isBusy({ queued: 1 }), isBusy({ running: 1 })]).toEqual([
      false,
      false,
      true,
      true,
    ])
  })
  it('says when the list is cut, and how to find the rest', () => {
    expect(describeMore({ items: Array(200), total: 1340 })).toBe(
      `Showing the newest 200 of ${format.count(1340, 'job')}. Choose Failed or Waiting to find older ones that need you.`,
    )
    expect(describeMore({ items: Array(200), total: null })).toBe(
      'Showing the newest 200. Choose Failed or Waiting to find older ones that need you.',
    )
  })
})
