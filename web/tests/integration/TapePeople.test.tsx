import { HttpResponse, http } from 'msw'
import { expectOneFilledButton } from 'strata-react/testing'
import { describe, expect, it } from 'vitest'
import { peopleNaming, runError, runPartWay, startNaming } from '../../src/mocks/handlers'
import { peopleWaiting, tapePeople } from '../../src/mocks/tape'
import { openApp, screen, waitFor, within } from '../utils/render'
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
    expect(within(asked).getAllByRole('img', { name: /^Face \d of the group$/ })).toHaveLength(5)
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
