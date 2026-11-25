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

export default defineVisualConfig({
  storybook: 'storybook-static',
  // Off for now: the CI runner's Chromium makes the window 1x1 for a moment while it takes a whole-page shot, each
  // FilmStrip re-picks its frames for that width and back, and the new Thumbnails below the window wait to be near it
  // before asking for their pictures, so the tape page's strips were pictures in one pass and skeletons in the next
  // (measured on the runner; strata-react issue #352). With pictures below the window never asked for, they are
  // skeletons in every pass. Turn it back on once strata-react shows a picture it has loaded at once on a remount.
  scrollThrough: false,
  app: {
    dir: 'dist-visual',
    handlers,
    viewports: ['wide', 'phone'],
    routes: [
      {
        name: 'sources',
        path: '/',
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
