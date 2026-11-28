import { HttpResponse, http } from 'msw'
import { expectOneFilledButton } from 'strata-react/testing'
import { describe, expect, it, vi } from 'vitest'
import { peopleLongGroup, peopleNaming, runError, runPartWay, startNaming } from '../../src/mocks/handlers'
import { peopleWaiting, tapePeople } from '../../src/mocks/tape'
import { act, openApp, screen, waitFor, within } from '../utils/render'
import { mock, server } from '../utils/server'

// The tape page's People tab over the mock server's christmas-1996.mkv: Tom, Ella and Grandma Rose named, or (after
// `startNaming`) three groups of faces left to answer before the scenes are described.

// By its heading's text, not its role: a role query walks the whole tape page and can outlast findBy's second.
const group = async () => (await screen.findByText('Who is this?', { selector: 'h3' })).closest('section')!

describe('the people on a tape', () => {
  it('lists the people named on the tape, longest on screen first, and says what was set aside', async () => {
    openApp('/sources/5?view=people')
    expect(await screen.findByText(/^3\speople named; 4\stracks skipped; 1\strack not faces; 9\stracks too small/)).toBeInTheDocument()
    const named = screen.getByRole('region', { name: 'Named on this tape' })
    expect(
      within(named)
        .getAllByRole('listitem')
        .map(li => li.querySelector('p')?.textContent),
    ).toEqual(['Tom', 'Ella', 'Grandma Rose'])
    expect(screen.getByText('Everyone is answered')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Who is this?' })).toBeNull()
  })

  it('shows who is in each scene and what they do', async () => {
    const { user } = openApp('/sources/5')
    const list = await screen.findByRole('list', { name: 'Chapters' })
    const first = within(list).getByRole('listitem', { name: 'Scene 1' })
    expect(first).toHaveTextContent('Tom, Ella and Grandma Rose')
    await user.click(within(first).getByRole('button', { name: 'What scene 1 shows, and why' }))
    expect(within(first).getByText('Who does what')).toBeInTheDocument()
    expect(within(first).getByText('Tom waves at the camera')).toBeInTheDocument()
  })

  it('asks for one group at a time, the suggestion the one filled button, and answers it with Undo in reach', async () => {
    startNaming()
    const { user } = openApp('/sources/5?view=people')
    let asked = await group()
    expect(within(asked).getByText('3 groups left')).toBeInTheDocument()
    expect(within(asked).getAllByRole('img', { name: /^Face \d of the group$/ })).toHaveLength(4) // one per track
    expectOneFilledButton()
    await user.click(within(asked).getByRole('button', { name: 'This is Uncle Pete' }))
    asked = await group()
    expect(within(asked).getByText('2 groups left')).toBeInTheDocument()
    expect(screen.getByText('Named Uncle Pete.')).toBeInTheDocument()
    // nothing has gone to the server yet: Undo brings the group back
    expect(mock.people[5].groups).toHaveLength(3)
    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(within(await group()).getByText('3 groups left')).toBeInTheDocument()
    // answering the next commits the one before
    await user.click(within(await group()).getByRole('button', { name: 'This is Uncle Pete' }))
    asked = await group()
    expectOneFilledButton() // no suggestion: the name's own button is the filled one
    await user.click(within(asked).getByRole('button', { name: 'Name them' }))
    expect(within(asked).getByText('Type their name, or skip the group.')).toBeInTheDocument()
    await user.type(within(asked).getByRole('textbox', { name: 'Their name' }), 'Auntie Jean')
    await user.click(within(asked).getByRole('button', { name: 'Name them' }))
    await waitFor(() => expect(mock.people[5].people.map(p => p.name)).toContain('Uncle Pete'))
    expect(await screen.findByRole('region', { name: 'Named on this tape' })).toHaveTextContent('Uncle Pete')
    // an answer still in its window is sent when the page goes, which would land in the next test's mock: take it back
    await user.click(screen.getByRole('button', { name: 'Undo' }))
  })

  it('names some of a group that holds two people, and leaves the rest to answer', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    startNaming()
    const { user } = openApp('/sources/5?view=people', { user: { advanceTimers: vi.advanceTimersByTime } })
    let asked = await group()
    const faces = within(asked).getByRole('grid', { name: 'Their faces' })
    // the first and third faces are someone else: chosen, the answer is for them only
    await user.click(within(faces).getByRole('row', { name: /face 1, at 20:10/ }))
    await user.click(within(faces).getByRole('row', { name: /face 3, at 43:24/ }))
    expect(screen.getByText(/The answer below is for these faces only; 2\sfaces stay\./)).toBeInTheDocument()
    expectOneFilledButton()
    await user.type(within(asked).getByRole('textbox', { name: 'Someone else: their name' }), 'Cousin Sam')
    await user.click(within(asked).getByRole('button', { name: 'Name them' }))
    expect(screen.getByText('Named Cousin Sam (2 faces).')).toBeInTheDocument()
    // the rest of the group is still asked about, its two faces, the choice cleared
    asked = await group()
    expect(within(asked).getByText('3 groups left')).toBeInTheDocument()
    expect(within(asked).getAllByRole('img', { name: /^Face \d of the group$/ })).toHaveLength(2)
    expect(within(asked).getByRole('button', { name: 'Play from 0:31' })).toBeInTheDocument()
    expect(screen.queryByText(/The answer below is for/)).toBeNull()
    // the answer commits when its window runs out, while the rest is being answered: what was typed and chosen stays
    await user.type(within(asked).getByRole('textbox', { name: 'Someone else: their name' }), 'Aun')
    await user.click(within(within(asked).getByRole('grid', { name: 'Their faces' })).getByRole('row', { name: /face 1,/ }))
    await act(() => vi.advanceTimersByTimeAsync(9000))
    await waitFor(() => expect(mock.people[5].groups[0].tracks).toEqual(['t3', 't22']))
    asked = await group()
    expect(within(asked).getByRole('textbox', { name: 'Someone else: their name' })).toHaveValue('Aun')
    expect(screen.getByText(/The answer below is for this face only; 1\sface stay/)).toBeInTheDocument()
    // the next answer is for the chosen face only
    await user.click(within(asked).getByRole('button', { name: 'This is Uncle Pete' }))
    await act(() => vi.advanceTimersByTimeAsync(9000))
    await waitFor(() => expect(mock.people[5].people.find(p => p.name === 'Uncle Pete')?.tracks).toBe(1))
    expect(mock.people[5].people.find(p => p.name === 'Cousin Sam')?.tracks).toBe(2)
    vi.useRealTimers()
  })

  it('keeps a long group’s faces in a box of their own, any of them to choose', async () => {
    server.use(...peopleLongGroup)
    const { user } = openApp('/sources/5?view=people')
    const asked = await group()
    expect(within(asked).getByText(/, seen 36 times$/)).toBeInTheDocument()
    const faces = within(asked).getByRole('grid', { name: 'Their faces' })
    await user.click(await within(faces).findByRole('row', { name: /^face 1, at 0:40/ }))
    expect(screen.getByText(/The answer below is for this face only; 35\sfaces stay\./)).toBeInTheDocument()
    await user.click(within(asked).getByRole('button', { name: 'Skip' }))
    expect(screen.getByText('Skipped 1 face.')).toBeInTheDocument()
    expect(within(await group()).getByText(/, seen 35 times$/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Undo' }))
  })

  it('adds a group to someone named before, skips one, or says it is not faces', async () => {
    startNaming()
    const { user } = openApp('/sources/5?view=people')
    let asked = await group()
    await user.selectOptions(await within(asked).findByRole('combobox', { name: 'Or someone named before' }), '2') // Tom
    await user.click(within(asked).getByRole('button', { name: 'Add to them' }))
    asked = await group()
    await user.click(within(asked).getByRole('button', { name: 'Skip' }))
    asked = await group()
    expect(within(asked).getByText('The last group')).toBeInTheDocument()
    // the group's first sight plays in the player beside: the contents mark the chapter it is in
    await user.click(within(asked).getByRole('button', { name: 'Play from 1:28:22' }))
    const contents = screen.getByRole('list', { name: 'Chapters of the tape' })
    expect(
      within(contents)
        .getAllByRole('listitem')
        .map(i => i.getAttribute('aria-current')),
    ).toEqual([null, 'time', null])
    await user.click(within(asked).getByRole('button', { name: 'Not faces' }))
    expect(await screen.findByText('Every group is answered')).toBeInTheDocument()
    await waitFor(() => expect(mock.people[5].skipped).toBe(2))
    expect(mock.people[5].people.find(p => p.name === 'Tom')?.tracks).toBe(42)
    await user.click(screen.getByRole('button', { name: 'Undo' }))
  })

  it('skips the rest, which lets the scenes be described', async () => {
    startNaming()
    const { user } = openApp('/sources/5?view=people')
    await group()
    expect(screen.getByText(/The scenes are described once the people are named/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Skip the rest' }))
    await waitFor(() => expect(mock.runs[5].stages.find(s => s.name === 'name_people')?.state).toBe('done'))
    expect(mock.people[5].done).toBe(true)
  })

  it('points the scenes to the People tab while the descriptions wait for names', async () => {
    server.use(...peopleNaming)
    openApp('/sources/5')
    expect(
      await screen.findByText(/; not described yet; 2 starts to check; people to name in the People tab first; chapters still to come\.$/),
    ).toBeInTheDocument()
  })

  it('says what the faces wait for while the tape’s job runs', async () => {
    server.use(
      runPartWay,
      http.get('*/api/sources/:id/people', () => HttpResponse.json(peopleWaiting())),
    )
    openApp('/sources/5?view=people')
    expect(await screen.findByText('Faces are followed once the shots are found.')).toBeInTheDocument()
    expect(screen.queryByText(/answered$/)).toBeNull()
  })

  it('says a tape has no faces at all', async () => {
    server.use(http.get('*/api/sources/:id/people', () => HttpResponse.json({ ...peopleWaiting(), waiting: null })))
    openApp('/sources/5?view=people')
    expect(await screen.findByText('No faces on this tape')).toBeInTheDocument()
  })

  it('lists someone added without a name or a face to show, seen once', async () => {
    const people = { ...tapePeople, people: [{ id: 9, name: null, tracks: 1, seconds: 4, face: null }] }
    server.use(http.get('*/api/sources/:id/people', () => HttpResponse.json(people)))
    openApp('/sources/5?view=people')
    const named = await screen.findByRole('region', { name: 'Named on this tape' })
    expect(named).toHaveTextContent('Person 9')
    expect(named).toHaveTextContent('0:04 on screen, seen once')
    expect(within(named).getByText('No picture')).toBeInTheDocument()
  })

  it('still asks about the faces when the steps cannot be read, without offering to skip them', async () => {
    startNaming()
    server.use(runError)
    openApp('/sources/5?view=people')
    await group()
    expect(screen.queryByRole('button', { name: 'Skip the rest' })).toBeNull()
  })

  it('says when the people named before cannot be read, rather than listing nobody', async () => {
    startNaming()
    server.use(http.get('*/api/people', () => new HttpResponse(null, { status: 503 })))
    openApp('/sources/5?view=people')
    const asked = await group()
    expect(await within(asked).findByRole('alert')).toBeInTheDocument()
    expect(within(asked).queryByRole('combobox', { name: 'Or someone named before' })).toBeNull()
  })

  it('says nobody else is named yet when the only person known is the one suggested', async () => {
    startNaming()
    server.use(http.get('*/api/people', () => HttpResponse.json([{ id: 4, name: 'Uncle Pete', faces: 12 }])))
    openApp('/sources/5?view=people')
    expect(await within(await group()).findByText('Nobody else is named on any tape yet.')).toBeInTheDocument()
  })
})
