import { describe, expect, it } from 'vitest'
import { jobsEmpty, jobsError, jobsMore } from '../../src/mocks/handlers'
import { openApp, screen, waitFor, within } from '../utils/render'
import { mock, server } from '../utils/server'

describe('Jobs', () => {
  it('answers what is running, waiting and failed, and names each job in words', async () => {
    openApp('/jobs')
    const table = await screen.findByRole('grid', { name: /Jobs, newest first/ })
    expect(screen.getByText('1 running, 1 waiting, 1 failed.')).toBeInTheDocument()
    // one job per tape, its progress the step it is on
    const [running, cancelled] = within(table).getAllByRole('row', { name: /Analyse beach\.mp4/ })
    expect(running).toHaveTextContent('Find the shots')
    expect(cancelled).toHaveTextContent('Cancelled')
    expect(within(table).getByRole('row', { name: /Analyse wedding-1994-side-b\.mkv/ })).toHaveTextContent('strata-models is not answering')
  })

  it('runs a failed job again', async () => {
    const { user } = openApp('/jobs')
    await user.click(await screen.findByRole('button', { name: 'Run again Analyse wedding-1994-side-b.mkv' }))
    await waitFor(() => expect(mock.jobs.find(j => j.id === 7)?.state).toBe('queued'))
    expect(await screen.findByText('1 running, 2 waiting.')).toBeInTheDocument()
  })

  it('cancels a waiting job', async () => {
    const { user } = openApp('/jobs')
    await user.click(await screen.findByRole('button', { name: 'Cancel Analyse interview.wav' }))
    await waitFor(() => expect(mock.jobs.find(j => j.id === 8)?.state).toBe('cancelled'))
  })

  it('shows only the failed jobs, with the filter in the URL', async () => {
    const { user, router } = openApp('/jobs')
    await screen.findByRole('grid', { name: /Jobs/ })
    await user.click(screen.getByRole('radio', { name: 'Failed' }))
    expect(router.state.location.search).toBe('?state=failed')
    await waitFor(() => expect(within(screen.getByRole('grid', { name: /Jobs/ })).getAllByRole('row')).toHaveLength(2))
  })

  it('counts the whole queue, not the page, and says when the list is cut', async () => {
    server.use(jobsMore)
    const { user } = openApp('/jobs')
    expect(await screen.findByText(/Showing the newest 6 of 1,340\sjobs\./)).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Failed' }))
    // the finding is the queue's, whichever state the list shows
    expect(await screen.findByText('1 running, 1 waiting, 1 failed.')).toBeInTheDocument()
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
