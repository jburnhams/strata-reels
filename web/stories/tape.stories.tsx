import type { Meta, StoryObj } from '@storybook/react-vite'
import { peaksLoading, scenesError, scenesLoading, scenesPartWay, scenesWaiting, transcriptWaiting } from '../src/mocks/handlers'
import { AppAt } from './app-at'

// A tape's page (christmas-1996.mkv in the mock server): the player, the tape's sound and its scenes,
// in each state the scenes read can be in.

const meta = {
  title: 'Screens/Tape',
  render: () => <AppAt route="/sources/5" />,
  tags: ['visual-wide'],
} satisfies Meta
export default meta
type Story = StoryObj<typeof meta>

export const Ready: Story = {}
/** A scene opened: what it saw, what it guessed and why its start is a boundary (?scene=3 in the URL). */
export const SceneOpen: Story = { render: () => <AppAt route="/sources/5?scene=3" /> }
export const Transcript: Story = { render: () => <AppAt route="/sources/5?view=transcript" /> }
export const TranscriptToCome: Story = {
  render: () => <AppAt route="/sources/5?view=transcript" />,
  beforeEach: ({ msw }) => msw.use(transcriptWaiting),
}
/** Grouped into scenes, but nothing described or transcribed yet. */
export const PartWay: Story = { beforeEach: ({ msw }) => msw.use(scenesPartWay) }
/** Before the scenes are grouped: the finding names what they wait for. */
export const Waiting: Story = { beforeEach: ({ msw }) => msw.use(scenesWaiting) }
export const Loading: Story = { beforeEach: ({ msw }) => msw.use(scenesLoading) }
export const SoundLoading: Story = { beforeEach: ({ msw }) => msw.use(peaksLoading) }
export const ServerError: Story = { beforeEach: ({ msw }) => msw.use(scenesError) }
export const Phone: Story = { tags: ['visual-phone'] }
