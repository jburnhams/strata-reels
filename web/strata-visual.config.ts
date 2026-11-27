// The visual suite, run by strata-react's own runner (`strata-visual`, from strata-react/visual):
// Playwright Chromium screenshots, light and dark, compared with committed baselines in
// tests/visual/baselines/ by pixelmatch. Two targets:
//
// - storybook: every story of the built Storybook (each screen in each server state);
// - app: the production build served as the Python server would (history fallback), each route at
//   a wide and a phone viewport, with every request answered in Node by the same MSW handlers the
//   jsdom tests use. Route states here are the ones a story cannot reach without a click: a field's
//   error. None of them writes to the mock server, so shots cannot affect each other.
//
// `npm run test:visual` builds both and compares; `npm run test:visual:update` writes new
// baselines. Look at a diff in tests/visual/output/ before accepting it.
import { defineVisualConfig } from 'strata-react/visual'
import { handlers } from './src/mocks/handlers'
import { pinMockClock } from './src/mocks/tape'

// The fixtures' morning: the System page's uptime and "Updated" time read the page's clock, and the jobs'
// "minutes ago" read it against the handlers' times, which run in Node and are pinned to the same moment.
const TIME = '2026-11-09T09:00:00Z'
pinMockClock(TIME)

export default defineVisualConfig({
  time: TIME,
  storybook: 'storybook-static',
  app: {
    dir: 'dist-visual',
    handlers,
    viewports: ['wide', 'phone'],
    routes: [
      {
        name: 'sources',
        path: '/sources',
        states: {
          ready: [],
          refused: {
            setup: async page => {
              await page.getByRole('textbox', { name: 'Add footage' }).fill('/missing/tape.mkv')
              await page.getByRole('button', { name: 'Add' }).click()
              // The sentence is under the field and in the button's announcement, so wait on the field turning invalid.
              await page.locator('input[aria-invalid="true"]').waitFor()
            },
          },
        },
      },
      { name: 'tape', path: '/sources/5' },
      { name: 'jobs', path: '/jobs' },
      { name: 'system', path: '/system' },
      { name: 'settings', path: '/settings' },
    ],
  },
})
