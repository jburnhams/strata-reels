import { delay, HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { addFails, sourcesEmpty, sourcesError } from '../../src/mocks/handlers'
import { tapeRunDone } from '../../src/mocks/tape'
import { openApp, screen, waitFor, within } from '../utils/render'
import { mock, server } from '../utils/server'

describe('Sources', () => {
  it('lists the footage, newest first, and answers with how much there is', async () => {
    openApp('/')
    const table = await screen.findByRole('grid', { name: /Sources, newest first/ })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(5)
    expect(rows[0]).toHaveTextContent('christmas-1996.mkv')
    expect(rows[0]).toHaveTextContent('3:00:12')
    expect(rows[0]).toHaveTextContent(/720\s×\s576, 25\sfps, interlaced/)
    expect(within(table).getByRole('row', { name: /interview\.wav/ })).toHaveTextContent('No video')
    expect(
      await screen.findByText(/^5\ssources running 8:37:23 in all; 1\stape to check, 1 stopped at a failed step, 1 analysing\.$/),
    ).toBeInTheDocument()
  })

  it('says where each tape’s analysis stands, and sorts what needs the person first', async () => {
    const { user } = openApp('/sources')
    const table = await screen.findByRole('grid', { name: /Sources/ })
    const row = (name: RegExp) => within(table).getByRole('row', { name })
    await waitFor(() => expect(row(/christmas-1996/)).toHaveTextContent(/Needs review\s*Check the chapters/))
    expect(row(/beach\.mp4/)).toHaveTextContent(/Analysing\s*Find the shots, step 5 of 13/)
    expect(row(/interview\.wav/)).toHaveTextContent(/Failed\s*Find the faces/)
    expect(row(/wedding-1994-side-a/)).toHaveTextContent('Done')
    expect(row(/wedding-1994-side-b/)).toHaveTextContent(/Part done/)
    await user.click(within(table).getByRole('columnheader', { name: /Analysis/ }))
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('christmas-1996.mkv')
  })

  it('lists the footage while the analysis is still being read, and reads every page of it', async () => {
    server.use(http.get('*/api/workflows/tape/subjects', () => delay('infinite')))
    openApp('/sources')
    const table = await screen.findByRole('grid', { name: /Sources/ })
    expect(within(table).getByRole('row', { name: /christmas-1996/ })).not.toHaveTextContent('Needs review')
    expect(screen.getByText(/^5\ssources running 8:37:23 in all\.$/)).toBeInTheDocument()
  })

  it('reads the analysis a page at a time until the server says there is no more', async () => {
    const pages: (string | null)[] = []
    server.use(
      http.get('*/api/workflows/tape/subjects', ({ request }) => {
        const after = new URL(request.url).searchParams.get('after')
        pages.push(after)
        return HttpResponse.json(after ? { items: [], next: null } : { items: [tapeRunDone], next: 'source:5' })
      }),
    )
    const { user } = openApp('/sources')
    const table = await screen.findByRole('grid', { name: /Sources/ })
    await waitFor(() => expect(within(table).getByRole('row', { name: /christmas-1996/ })).toHaveTextContent('Needs review'))
    expect(pages).toEqual([null, 'source:5'])
    // Tapes with no run yet sort after those with one.
    await user.click(within(table).getByRole('columnheader', { name: /Analysis/ }))
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('christmas-1996.mkv')
  })

  it('says the analysis could not be read, with Try again, and keeps the list', async () => {
    let fail = true
    server.use(
      http.get('*/api/workflows/tape/subjects', () =>
        fail
          ? new HttpResponse(null, { status: 503, statusText: 'Service Unavailable' })
          : HttpResponse.json({ items: [tapeRunDone], next: null }),
      ),
    )
    const { user } = openApp('/sources')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Each tape’s analysis could not be read/)
    expect(screen.getByRole('grid', { name: /Sources/ })).toBeInTheDocument()
    fail = false
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    const table = screen.getByRole('grid', { name: /Sources/ })
    await waitFor(() => expect(within(table).getByRole('row', { name: /christmas-1996/ })).toHaveTextContent('Needs review'))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('keeps the sort of an older link to the list at the app’s root', async () => {
    const { router } = openApp('/?sort=length.desc')
    await screen.findByRole('grid', { name: /Sources/ })
    expect(router.state.location.pathname).toBe('/sources')
    expect(router.state.location.search).toBe('?sort=length.desc')
  })

  it('keeps the sort in the URL', async () => {
    const { user, router } = openApp('/')
    const table = await screen.findByRole('grid', { name: /Sources/ })
    await user.click(within(table).getByRole('columnheader', { name: /Length/ }))
    expect(router.state.location.search).toMatch(/sort=length/)
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('beach.mp4')
  })

  it('adds a file by its path and lists it', async () => {
    const { user } = openApp('/')
    await screen.findByRole('grid', { name: /Sources/ })
    await user.type(screen.getByRole('textbox', { name: 'Add footage' }), '/home/ada/tapes/1997.mkv')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByRole('row', { name: /1997\.mkv/ })).toBeInTheDocument()
    expect(mock.sources[0].path).toBe('/home/ada/tapes/1997.mkv')
    expect(screen.getByRole('textbox', { name: 'Add footage' })).toHaveValue('')
  })

  it('asks for a path before sending anything, and says why the server refused one', async () => {
    const { user } = openApp('/')
    const field = await screen.findByRole('textbox', { name: 'Add footage' })
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(field).toHaveAccessibleDescription(/Enter the path of a video file/)
    expect(field).toBeInvalid()
    await user.type(field, '/missing/tape.mkv')
    expect(field).not.toBeInvalid()
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(field).toHaveAccessibleDescription(/^The request was refused: there is no file \/missing\/tape\.mkv\./))
    expect(mock.sources).toHaveLength(5)
  })

  it('says what happened when adding fails', async () => {
    server.use(addFails)
    const { user } = openApp('/')
    await user.type(await screen.findByRole('textbox', { name: 'Add footage' }), '/home/ada/tapes/1997.mkv')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByRole('textbox', { name: 'Add footage' })).toHaveAccessibleDescription(/The server could not answer \(503\)/)
  })

  it('teaches how to add the first file', async () => {
    server.use(sourcesEmpty)
    openApp('/')
    expect(await screen.findByText('No footage yet')).toBeInTheDocument()
    expect(screen.getByText('./app add /path/to/tape.mkv')).toBeInTheDocument()
    expect(screen.getByText('No footage yet. Add a tape transfer or a clip below.')).toBeInTheDocument()
  })

  it('says the server could not answer', async () => {
    server.use(sourcesError)
    openApp('/')
    expect(await screen.findByRole('alert')).toHaveTextContent('The server could not answer (503)')
  })
})
