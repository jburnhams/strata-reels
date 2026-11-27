import type { Meta, StoryObj } from '@storybook/react-vite'
import { reelsEmpty, reelsError, reelsLoading } from '../src/mocks/handlers'
import { AppAt } from './app-at'

// The reels list: the tape's reel with a current version and one waiting, and side A's, not accepted yet.

const meta = {
  title: 'Screens/Reels',
  render: () => <AppAt route="/reels" />,
  tags: ['visual-wide'],
} satisfies Meta
export default meta
type Story = StoryObj<typeof meta>

export const Ready: Story = {}
export const Loading: Story = { beforeEach: ({ msw }) => msw.use(reelsLoading) }
export const Empty: Story = { beforeEach: ({ msw }) => msw.use(reelsEmpty) }
export const ServerError: Story = { beforeEach: ({ msw }) => msw.use(reelsError) }
export const Phone: Story = { tags: ['visual-phone'] }
