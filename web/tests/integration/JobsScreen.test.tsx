import { describe, expect, it } from 'vitest'
import { jobsEmpty, jobsError } from '../../src/mocks/handlers'
import { openApp, screen, waitFor, within } from '../utils/render'
import { mock, server } from '../utils/server'

describe('Jobs', () => {
  it('answers what is running, waiting and failed, and names each job in words', async () => {
    openApp('/jobs')
    const table = await screen.findByRole('grid', { name: /Jobs, newest first/ })
    expect(screen.getByText('1 running, 1 waiting, 1 failed.')).toBeInTheDocument()
    expect(within(table).getByRole('row', { name: /Describe the scenes in christmas-1996\.mkv/ })).toHaveTextContent('Scene 4 of 6')
    expect(within(table).getByRole('row', { name: /Find the faces in christmas-1996\.mkv/ })).toHaveTextContent(
      'strata-models is not answering',
    )
    expect(within(table).getByRole('row', { name: /Read all of beach\.mp4/ })).toHaveTextContent('Cancelled')
  })

  it('runs a failed job again', async () => {
    const { user } = openApp('/jobs')
    await user.click(await screen.findByRole('button', { name: 'Run again Find the faces in christmas-1996.mkv' }))
    await waitFor(() => expect(mock.jobs.find(j => j.id === 13)?.state).toBe('queued'))
    expect(await screen.findByText('1 running, 2 waiting.')).toBeInTheDocument()
  })

  it('cancels a waiting job', async () => {
    const { user } = openApp('/jobs')
    await user.click(await screen.findByRole('button', { name: 'Cancel Write down what is said in christmas-1996.mkv' }))
    await waitFor(() => expect(mock.jobs.find(j => j.id === 10)?.state).toBe('cancelled'))
  })

  it('shows only the failed jobs, with the filter in the URL', async () => {
    const { user, router } = openApp('/jobs')
    await screen.findByRole('grid', { name: /Jobs/ })
    await user.click(screen.getByRole('radio', { name: 'Failed' }))
    expect(router.state.location.search).toBe('?state=failed')
    await waitFor(() => expect(within(screen.getByRole('grid', { name: /Jobs/ })).getAllByRole('row')).toHaveLength(2))
  })

  it('says when there is nothing in the queue, and when the server is down', async () => {
    server.use(jobsEmpty)
    const { unmount } = openApp('/jobs')
    expect(await screen.findByText('No jobs yet')).toBeInTheDocument()
    unmount()
    server.use(jobsError)
    openApp('/jobs')
    expect(await screen.findByRole('alert')).toHaveTextContent('The server could not answer (503)')
  })
})
