import type { Meta, StoryObj } from '@storybook/react-vite'
import { jobsEmpty, jobsError, jobsLoading } from '../src/mocks/handlers'
import { AppAt } from './app-at'

const meta = {
  title: 'Screens/Jobs',
  render: () => <AppAt route="/jobs" />,
  tags: ['visual-wide'],
} satisfies Meta
export default meta
type Story = StoryObj<typeof meta>

export const Ready: Story = {}
/** Only the failed jobs, from the URL (?state=failed). */
export const Failed: Story = { render: () => <AppAt route="/jobs?state=failed" /> }
export const Loading: Story = { beforeEach: ({ msw }) => msw.use(jobsLoading) }
export const Empty: Story = { beforeEach: ({ msw }) => msw.use(jobsEmpty) }
export const ServerError: Story = { beforeEach: ({ msw }) => msw.use(jobsError) }
export const Phone: Story = { tags: ['visual-phone'] }
