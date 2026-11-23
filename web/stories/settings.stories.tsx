import type { Meta, StoryObj } from '@storybook/react-vite'
import { AppAt } from './app-at'

const meta = {
  title: 'Screens/Settings',
  render: () => <AppAt route="/settings" />,
  tags: ['visual-wide'],
} satisfies Meta
export default meta
type Story = StoryObj<typeof meta>

export const Appearance: Story = {}
export const Phone: Story = { tags: ['visual-phone'] }
