import type { Meta, StoryObj } from '@storybook/react-vite'
import { addFails, sourcesEmpty, sourcesError, sourcesLoading } from '../src/mocks/handlers'
import { AppAt } from './app-at'

const meta = {
  title: 'Screens/Sources',
  render: () => <AppAt route="/" />,
  tags: ['visual-wide'],
} satisfies Meta
export default meta
type Story = StoryObj<typeof meta>

export const Ready: Story = {}
/** Sorted longest first, from the URL (?sort=length.desc). */
export const SortedByLength: Story = { render: () => <AppAt route="/?sort=length.desc" /> }
export const Loading: Story = { beforeEach: ({ msw }) => msw.use(sourcesLoading) }
export const Empty: Story = { beforeEach: ({ msw }) => msw.use(sourcesEmpty) }
export const ServerError: Story = { beforeEach: ({ msw }) => msw.use(sourcesError) }
/** Adding fails; enter a path and press Add to see it. */
export const AddingFails: Story = { beforeEach: ({ msw }) => msw.use(addFails), tags: ['no-visual'] }
export const Phone: Story = { tags: ['visual-phone'] }
