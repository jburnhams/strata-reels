import type { Meta, StoryObj } from '@storybook/react-vite'
import {
  peaksLoading,
  runChecked,
  runFailed,
  runPartWay,
  runWaiting,
  scenesCorrected,
  scenesError,
  scenesLoading,
  scenesPartWay,
  scenesRegrouping,
  scenesUnnamed,
  scenesWaiting,
  transcriptWaiting,
} from '../src/mocks/handlers'
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

/** Every step done: three chapters of the six scenes, each named. */
export const Ready: Story = {}
/** The chapters grouped, their names part way: the second is shown by its number. */
export const ChapterUnnamed: Story = { beforeEach: ({ msw }) => msw.use(scenesUnnamed) }
/** Corrected and confirmed: two scenes marked as the grouping had them, and chapter 2 under the person's title. */
export const Corrected: Story = { beforeEach: ({ msw }) => msw.use(scenesCorrected, runChecked) }
/** A mark made, the chapters not grouped by it yet. */
export const Regrouping: Story = { beforeEach: ({ msw }) => msw.use(scenesRegrouping) }
/** A scene opened: what it saw, what it guessed and why its start is a boundary (?scene=3 in the URL). */
export const SceneOpen: Story = { render: () => <AppAt route="/sources/5?scene=3" /> }
export const Transcript: Story = { render: () => <AppAt route="/sources/5?view=transcript" /> }
export const TranscriptToCome: Story = {
  render: () => <AppAt route="/sources/5?view=transcript" />,
  beforeEach: ({ msw }) => msw.use(transcriptWaiting),
}
/** Grouped into scenes, but nothing described or transcribed yet; the tape's job is on the browser copy. */
export const PartWay: Story = { beforeEach: ({ msw }) => msw.use(scenesPartWay, runPartWay) }
/** Before the scenes are grouped: the finding names what they wait for, and the steps show how far it has got. */
export const Waiting: Story = { beforeEach: ({ msw }) => msw.use(scenesWaiting, runWaiting) }
/** Every step done (?view=steps in the URL). */
export const Steps: Story = { render: () => <AppAt route="/sources/5?view=steps" /> }
/** Part way: the job's progress over the steps. */
export const StepsRunning: Story = {
  render: () => <AppAt route="/sources/5?view=steps" />,
  beforeEach: ({ msw }) => msw.use(scenesPartWay, runPartWay),
}
/** A step failed: the finding names it, and the step says why with Try again. */
export const StepsFailed: Story = {
  render: () => <AppAt route="/sources/5?view=steps" />,
  beforeEach: ({ msw }) => msw.use(runFailed),
}
/** The failed step on a phone. */
export const StepsFailedPhone: Story = {
  render: () => <AppAt route="/sources/5?view=steps" />,
  beforeEach: ({ msw }) => msw.use(runFailed),
  tags: ['visual-phone'],
}
export const Loading: Story = { beforeEach: ({ msw }) => msw.use(scenesLoading) }
export const SoundLoading: Story = { beforeEach: ({ msw }) => msw.use(peaksLoading) }
export const ServerError: Story = { beforeEach: ({ msw }) => msw.use(scenesError) }
export const Phone: Story = { tags: ['visual-phone'] }
