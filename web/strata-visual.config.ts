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

import type { Page } from 'playwright'
import { defineVisualConfig } from 'strata-react/visual'
import { handlers } from './src/mocks/handlers'

/**
 * Waits, with the window as tall as the page, until every picture on it has loaded. strata-react 1.3.15's runner
 * does this for 3 s and then shoots whatever is there; the tape page has about sixty thumbnails answered by MSW in
 * Node, and on the CI runner some passes ran out of time, so one pass shot the lower filmstrips as skeletons and the
 * next as pictures (strata-react issue #331, fixed on its main with a 15 s wait and not yet released). Remove this
 * once strata-react is past 1.3.15.
 */
async function waitForPictures(page: Page) {
  const size = page.viewportSize()
  if (!size) return
  const height = await page.evaluate(() => document.documentElement.scrollHeight)
  await page.setViewportSize({ width: size.width, height: Math.min(height, 20_000) })
  await page.waitForFunction(
    () => [...document.images].every(img => img.complete) && !document.querySelector('[aria-busy="true"]'),
    undefined,
    { timeout: 30_000 },
  )
  await page.setViewportSize(size)
  // TEMPORARY diagnostics for the unstable tape shot on CI: say what changes on the page after this point.
  const t0 = Date.now()
  const say = (what: string) => console.log(`DIAG ${page.viewportSize()?.width} +${Date.now() - t0}ms ${what}`)
  page.on('request', r => say(`request ${new URL(r.url()).pathname}`))
  page.on('console', m => m.text().startsWith('DIAG') && say(m.text()))
  await page.evaluate(() => {
    const busy = () => document.querySelectorAll('[aria-busy="true"]').length
    let last = busy()
    console.log(`DIAG busy ${last} images ${document.images.length}`)
    new MutationObserver(() => {
      const now = busy()
      if (now !== last) console.log(`DIAG busy ${last} -> ${now}, images ${document.images.length}, at ${location.pathname}${location.search}`)
      last = now
    }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-busy'] })
    const v = document.querySelector('video')
    for (const e of ['error', 'loadeddata', 'emptied', 'durationchange', 'seeked'])
      v?.addEventListener(e, () => console.log(`DIAG video ${e} ${v.readyState} ${v.currentTime}`))
    window.addEventListener('resize', () => console.log(`DIAG resize ${innerWidth}x${innerHeight}`))
  })
}

export default defineVisualConfig({
  storybook: 'storybook-static',
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
      { name: 'tape', path: '/sources/5', setup: waitForPictures },
      { name: 'jobs', path: '/jobs' },
      { name: 'system', path: '/system' },
      { name: 'settings', path: '/settings' },
    ],
  },
})
