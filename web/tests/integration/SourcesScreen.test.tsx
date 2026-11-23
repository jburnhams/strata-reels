import { describe, expect, it } from 'vitest'
import { addFails, sourcesEmpty, sourcesError } from '../../src/mocks/handlers'
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
    expect(screen.getByText(/^5\ssources running 8:37:23 in all\.$/)).toBeInTheDocument()
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
