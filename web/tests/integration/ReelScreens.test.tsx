import { describe, expect, it } from 'vitest'
import { acceptConflict, reelError, reelStale, reelsEmpty, renderFailed, renderQueued, renderRefused } from '../../src/mocks/handlers'
import { renderOf } from '../../src/mocks/reels'
import { openApp, screen, waitFor, within } from '../utils/render'
import { mock, server } from '../utils/server'

describe('Reels', () => {
  it('lists the reels and says which wait for the person', async () => {
    openApp('/reels')
    const table = await screen.findByRole('grid', { name: /Reels, newest first/ })
    expect(screen.getByText(/^2\sreels\. 2\sreels have versions for you to accept\.$/)).toBeInTheDocument()
    expect(within(table).getByRole('row', { name: /Wedding highlights/ })).toHaveTextContent('Not chosen')
    expect(within(table).getByRole('row', { name: /christmas-1996\.mkv, 2:00/ })).toHaveTextContent('2:00')
  })

  it('makes a reel of a tape and opens it on its first version', async () => {
    const { user, router } = openApp('/reels')
    await screen.findByRole('grid', { name: /Reels/ })
    const form = screen.getByRole('form', { name: 'Make a reel' })
    await waitFor(() => expect(within(form).getByRole('combobox', { name: 'Tape' })).toHaveValue('5'))
    const length = within(form).getByRole('textbox', { name: 'Length' })
    await user.clear(length)
    await user.type(length, '1:30')
    await user.click(within(form).getByRole('button', { name: 'Make a reel' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/reels/3'))
    expect(mock.reels.at(-1)?.target_seconds).toBe(90)
    expect(await screen.findByText(/No version is accepted yet\. 1\sproposal to accept or reject\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Accept this version' })).toBeInTheDocument()
  })

  it('refuses a length it cannot read, and says when a tape has no scenes to cut from', async () => {
    const { user } = openApp('/reels')
    const form = await screen.findByRole('form', { name: 'Make a reel' })
    const length = within(form).getByRole('textbox', { name: 'Length' })
    await user.clear(length)
    await user.type(length, 'soon')
    await user.click(within(form).getByRole('button', { name: 'Make a reel' }))
    expect(await screen.findByText(/Enter a length as minutes and seconds/)).toBeInTheDocument()
    await user.clear(length)
    await user.type(length, '60')
    await user.selectOptions(within(form).getByRole('combobox', { name: 'Tape' }), 'beach.mp4')
    await user.click(within(form).getByRole('button', { name: 'Make a reel' }))
    // the server's sentence, under the tape it is about
    await waitFor(() =>
      expect(within(form).getByRole('combobox', { name: 'Tape' })).toHaveAccessibleDescription(
        /beach\.mp4 has no scenes yet: a reel is cut from them/,
      ),
    )
    expect(mock.reels).toHaveLength(2)
  })

  it('says None where nothing waits, and asks for a tape when there is none', async () => {
    mock.reels[0].open = []
    mock.reels[0].proposals = 0
    mock.sources = []
    const { user } = openApp('/reels')
    const table = await screen.findByRole('grid', { name: /Reels/ })
    expect(within(table).getByRole('row', { name: /christmas/ })).toHaveTextContent('None')
    await user.click(screen.getByRole('button', { name: 'Make a reel' }))
    expect(await screen.findByText(/Add a tape on the Sources page first/)).toBeInTheDocument()
  })

  it('says how to make the first reel', async () => {
    server.use(reelsEmpty)
    openApp('/reels')
    expect(await screen.findByText('No reels yet')).toBeInTheDocument()
  })
})

describe('A reel', () => {
  it('previews the current version, lists its clips and offers its file', async () => {
    const { user } = openApp('/reels/1')
    expect(await screen.findByRole('heading', { level: 1, name: 'christmas-1996.mkv, 2:00' })).toBeInTheDocument()
    expect(screen.getByText(/Version 2 is current: 2:00 in 12\sclips\. 1\sproposal to accept or reject\./)).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Preview of version 2' })).toBeInTheDocument()
    const clips = screen.getByRole('grid', { name: /Clips of version 2/ })
    expect(within(clips).getAllByRole('row')).toHaveLength(13)
    // a clip's Play moves the preview to it
    await user.click(within(clips).getByRole('button', { name: 'Play clip 2' }))
    expect(await within(screen.getByRole('group', { name: 'Preview of version 2' })).findByText(/Clip 2 of 12/)).toBeInTheDocument()
    // rendered already: the file to download and its SHA-256
    const link = await screen.findByRole('link', { name: 'Download the reel' })
    expect(link).toHaveAttribute('href', expect.stringMatching(/api\/reels\/1\/renders\/1\/file$/))
    expect(link).toHaveAttribute('download')
    expect(screen.getByText(/^e3f1a9c0/)).toBeInTheDocument()
  })

  it('shows a proposal from the URL and accepts it', async () => {
    const { user, router } = openApp('/reels/1')
    const versions = await screen.findByRole('grid', { name: /open proposals/ })
    await user.click(within(versions).getByRole('button', { name: 'Preview version 3' }))
    expect(await screen.findByRole('grid', { name: /Clips of version 3/ })).toBeInTheDocument()
    expect(router.state.location.search).toBe('?version=3')
    await user.click(screen.getByRole('button', { name: 'Accept this version' }))
    expect(await screen.findByText(/Version 3 is current/)).toBeInTheDocument()
    expect(mock.reels[0].current?.id).toBe(3)
    // not rendered yet: Render is the page's one filled button now
    expect(await screen.findByRole('button', { name: 'Render this version' })).toHaveAttribute('data-variant', 'default')
  })

  it('rejects a proposal and plans another', async () => {
    const { user } = openApp('/reels/1?version=3')
    await user.click(await screen.findByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(mock.reels[0].open).toHaveLength(0))
    expect(await screen.findByText(/^Version 2 is current: 2:00 in 12\sclips\.$/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Plan another version' }))
    await waitFor(() => expect(mock.reels[0].open).toHaveLength(1))
    expect(mock.reels[0].open[0].planner.seed).toBe(1)
    expect(await screen.findByRole('grid', { name: /Clips of version 6/ })).toBeInTheDocument()
  })

  it('renders a version and follows its job', async () => {
    const { user } = openApp('/reels/1?version=3')
    await user.click(await screen.findByRole('button', { name: 'Render this version' }))
    expect(await screen.findByText(/Waiting its turn on the queue/)).toBeInTheDocument()
    expect(mock.renders[0]).toMatchObject({ revision_id: 3, state: 'queued' })
  })

  it('says why a render failed and offers it again', async () => {
    server.use(renderFailed)
    openApp('/reels/1')
    expect(await screen.findByText('christmas-1996.mkv has changed since it was added: add it again')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Render again' })).toBeInTheDocument()
  })

  it('shows a render waiting on the queue', async () => {
    server.use(renderQueued)
    openApp('/reels/1')
    expect(await screen.findByText(/Waiting its turn on the queue/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Render/ })).not.toBeInTheDocument()
  })

  it('does not offer Accept on a proposal made on an older version', async () => {
    server.use(reelStale)
    openApp('/reels/1?version=3')
    expect(await screen.findByText(/no longer current, so it cannot be accepted/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Accept this version' })).not.toBeInTheDocument()
    expect(screen.getByText(/1\sproposal out of date\./)).toBeInTheDocument()
  })

  it('says what the plan could not keep, and offers a proposal’s file outlined', async () => {
    mock.renders.push(renderOf(2, mock.reels[1].open[0], 'done'))
    openApp('/reels/2')
    expect(await screen.findByRole('region', { name: 'What the plan could not keep' })).toHaveTextContent('short of the 1:00 asked for')
    // Accept is the filled one; the download of a proposal steps down
    expect(await screen.findByRole('link', { name: 'Download the reel' })).toHaveAttribute('data-variant', 'outline')
    expect(screen.getByRole('button', { name: 'Accept this version' })).toHaveAttribute('data-variant', 'default')
  })

  it('says why an accept or a render was refused', async () => {
    server.use(acceptConflict, renderRefused)
    const { user } = openApp('/reels/1?version=3')
    await user.click(await screen.findByRole('button', { name: 'Accept this version' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The reel has changed since this proposal was made')
    await user.click(screen.getByRole('button', { name: 'Render this version' }))
    await waitFor(() => expect(screen.getAllByRole('alert').at(-1)).toHaveTextContent('not hashed yet'))
  })

  it('says when the server is down', async () => {
    server.use(reelError)
    openApp('/reels/1')
    expect(await screen.findByRole('alert')).toHaveTextContent('The server could not answer (503)')
  })
})
