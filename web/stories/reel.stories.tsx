import type { Meta, StoryObj } from '@storybook/react-vite'
import { reelError, reelLoading, reelStale, renderFailed, renderQueued } from '../src/mocks/handlers'
import { AppAt } from './app-at'

// A reel's page (the tape's reel in the mock server): the version shown played as one film, its clips, the open
// proposals and the version's file, in each state the reel and its render can be in.

const meta = {
  title: 'Screens/Reel',
  render: () => <AppAt route="/reels/1" />,
  tags: ['visual-wide'],
} satisfies Meta
export default meta
type Story = StoryObj<typeof meta>

/** The current version, rendered: the download is the filled button. */
export const Current: Story = {}
/** The open proposal, from the URL (?version=3): Accept is the filled button, Render steps down. */
export const Proposal: Story = { render: () => <AppAt route="/reels/1?version=3" /> }
/** A proposal made on an older version: it says to plan again instead of offering Accept. */
export const Stale: Story = { render: () => <AppAt route="/reels/1?version=3" />, beforeEach: ({ msw }) => msw.use(reelStale) }
/** No version accepted yet, and the plan could not fill the length asked for. */
export const Conflicts: Story = { render: () => <AppAt route="/reels/2" /> }
export const Rendering: Story = { beforeEach: ({ msw }) => msw.use(renderQueued) }
export const RenderFailed: Story = { beforeEach: ({ msw }) => msw.use(renderFailed) }
export const Loading: Story = { beforeEach: ({ msw }) => msw.use(reelLoading) }
export const ServerError: Story = { beforeEach: ({ msw }) => msw.use(reelError) }
export const Phone: Story = { tags: ['visual-phone'] }
