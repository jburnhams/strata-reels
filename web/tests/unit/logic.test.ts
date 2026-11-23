import { readFileSync } from 'node:fs'
import { colorSchemeScript, format } from 'strata-react'
import { describe, expect, it } from 'vitest'
import { initialSources, systemNearlyFull, systemStatus } from '../../src/mocks/fixtures'
import { describePicture, describeSources, framesPerSecond } from '../../src/screens/SourcesScreen'
import { describeDisk, homeDisk, ofSize } from '../../src/screens/SystemScreen'

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
    expect(homeDisk({ ...systemStatus, folders: [{ name: 'home', path: '/x', exists: false }] })).toBeUndefined()
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
