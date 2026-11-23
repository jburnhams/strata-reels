import { describe, expect, it } from 'vitest'
import { openApp, screen, waitFor } from '../utils/render'

describe('Settings', () => {
  it('changes the colour scheme of the page', async () => {
    const { user } = openApp('/settings')
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Dark' }))
    await waitFor(() => expect(document.documentElement).toHaveClass('dark'))
    await user.click(screen.getByRole('radio', { name: 'Light' }))
    await waitFor(() => expect(document.documentElement).not.toHaveClass('dark'))
  })
})
