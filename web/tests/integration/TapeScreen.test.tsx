import { HttpResponse, http } from 'msw'
import { expectOneFilledButton } from 'strata-react/testing'
import { describe, expect, it } from 'vitest'
import { runError, scenesError, scenesPartWay, scenesWaiting, transcriptWaiting } from '../../src/mocks/handlers'
import { tapeRunFailed, tapeRunPartWay, tapeScenes, waitingScenes } from '../../src/mocks/tape'
import { openApp, screen, waitFor, within } from '../utils/render'
import { mock, server } from '../utils/server'

// The tape's page (R1 step 4) over the mock server's christmas-1996.mkv: six scenes, five described.

describe('a tape', () => {
  it('opens from the sources list and answers with how many scenes and what is still to check', async () => {
    const { user, router } = openApp('/')
    await user.click(await screen.findByRole('link', { name: 'christmas-1996.mkv' }))
    expect(router.state.location.pathname).toBe('/sources/5')
    expect(await screen.findByRole('heading', { level: 1, name: 'christmas-1996.mkv' })).toBeInTheDocument()
    expect(screen.getByText(/^3\schapters of 6\sscenes over 2:59:48; 1\sscene not described yet; 2 starts to check\.$/)).toBeInTheDocument()
  })

  it('lists each scene with its frames, its description, its faces and what is said', async () => {
    openApp('/sources/5')
    const list = await screen.findByRole('list', { name: 'Chapters' })
    const scenes = within(list).getAllByRole('listitem', { name: /^Scene \d$/ })
    expect(scenes).toHaveLength(6)
    const first = scenes[0]
    expect(within(first).getByRole('toolbar', { name: 'Frames of scene 1' })).toBeInTheDocument()
    expect(first).toHaveTextContent('Two children stand by a decorated tree and wave at the camera.')
    expect(first).toHaveTextContent(/Up to 2\sfaces/)
    expect(first).toHaveTextContent('“Is it on? Is the red light on?')
    expect(within(scenes[4]).getByText('Not described yet.')).toBeInTheDocument()
    expect(within(scenes[4]).getByText(/Start unsure \(47%\)/)).toBeInTheDocument()
  })

  it('groups the scenes into named chapters, each with its summary, its guesses and any doubt', async () => {
    const { user } = openApp('/sources/5')
    const list = await screen.findByRole('list', { name: 'Chapters' })
    const chapters = within(list).getAllByRole('listitem', { name: /^Chapter \d/ })
    expect(chapters.map(c => within(c).getAllByRole('heading')[0].textContent)).toEqual([
      'Chapter 1: Christmas morning',
      'Chapter 2: Christmas dinner',
      'Chapter 3: The evening in front of the television',
    ])
    const [morning, dinner, evening] = chapters
    expect(within(morning).getByRole('list', { name: 'Scenes of chapter 1: christmas morning' })).toBeInTheDocument()
    expect(within(morning).getAllByRole('listitem', { name: /^Scene \d$/ })).toHaveLength(3)
    expect(morning).toHaveTextContent(/0:00 to 40:01, 40:01 long, 3\sscenes/)
    expect(morning).toHaveTextContent('open their presents with the family watching')
    expect(morning).toHaveTextContent('Guessed: Christmas Day, because pyjamas')
    expect(within(dinner).queryByText(/^Start unsure \(\d+%\):/)).toBeNull() // 97% sure: nothing to check (its scene 5 is)
    expect(within(evening).getByText(/Start unsure \(61%\): pictures differ \(0\.74\), described differently/)).toBeInTheDocument()
    expect(within(evening).getByText('May be more than one occasion')).toBeInTheDocument()
    expect(within(evening).queryByText(/^Guessed/)).toBeNull()
    await user.click(within(dinner).getByRole('button', { name: 'Play chapter 2' }))
    expect(within(dinner).getByRole('listitem', { name: 'Scene 4' })).toHaveAttribute('aria-current', 'time')
  })

  it('shows a chapter not named yet by its number and says the names are to come', async () => {
    const unnamed = structuredClone(tapeScenes)
    unnamed.chapters![1].name = null
    unnamed.analysis.named = 2
    server.use(http.get('*/api/sources/:id/scenes', () => HttpResponse.json(unnamed)))
    openApp('/sources/5')
    const list = await screen.findByRole('list', { name: 'Chapters' })
    const second = within(list).getByRole('listitem', { name: 'Chapter 2' })
    expect(second).toHaveTextContent('Not named yet: the name step writes a title and summary for each chapter.')
    expect(screen.getByText(/chapter names still to come\.$/)).toBeInTheDocument()
  })

  it('shows what a scene saw apart from what it guessed, and keeps the open scene in the URL', async () => {
    const { user, router } = openApp('/sources/5')
    const list = await screen.findByRole('list', { name: 'Chapters' })
    await user.click(within(list).getByRole('button', { name: 'What scene 4 shows, and why' }))
    expect(router.state.location.search).toContain('scene=3')
    const scene = within(list).getByRole('listitem', { name: 'Scene 4' })
    expect(scene).toHaveTextContent('paper hats')
    expect(scene).toHaveTextContent('Christmas dinner, because paper hats and crackers on the table')
    expect(scene).toHaveTextContent(/88%\ssure a new scene starts here: pictures differ \(0\.66\), black between\./)
  })

  it('says what an undescribed scene and the first scene rest on, and what a plain description leaves out', async () => {
    const plain = structuredClone(tapeScenes)
    const d = plain.scenes[2].description!
    Object.assign(d, { inferred: [], setting: 'unknown', model: null, picture: { quality: 'good', problems: [] } })
    plain.scenes[0].frames = [] // no thumbnail to stand as the player's poster
    server.use(http.get('*/api/sources/:id/scenes', () => HttpResponse.json(plain)))
    const { user } = openApp('/sources/5?scene=0&scene=4')
    const list = await screen.findByRole('list', { name: 'Chapters' })
    expect(within(list).getByRole('listitem', { name: 'Scene 1' })).toHaveTextContent('At the start of the tape.')
    expect(within(list).getByRole('listitem', { name: 'Scene 5' })).toHaveTextContent(
      'Not written yet: the describe job writes one for each scene.',
    )
    await user.click(within(list).getByRole('button', { name: 'What scene 3 shows, and why' }))
    const third = within(list).getByRole('listitem', { name: 'Scene 3' })
    expect(third).toHaveTextContent('Nothing beyond what is seen.')
    expect(third).toHaveTextContent('indoors or out not known')
    expect(third).toHaveTextContent(/PictureGood(?!:)/)
    expect(third).toHaveTextContent('Written by–')
  })

  it('plays a scene from its button or one of its frames, and marks the scene being played', async () => {
    const { user } = openApp('/sources/5')
    const list = await screen.findByRole('list', { name: 'Chapters' })
    expect(within(list).getByRole('listitem', { name: 'Scene 1' })).toHaveAttribute('aria-current', 'time')
    await user.click(within(list).getByRole('button', { name: 'Play scene 2' }))
    expect(within(list).getByRole('listitem', { name: 'Scene 2' })).toHaveAttribute('aria-current', 'time')
    expect(within(list).getByRole('listitem', { name: 'Scene 1' })).not.toHaveAttribute('aria-current')
    await user.click(within(within(list).getByRole('listitem', { name: 'Scene 4' })).getAllByRole('button', { name: /^Play from/ })[0])
    expect(within(list).getByRole('listitem', { name: 'Scene 4' })).toHaveAttribute('aria-current', 'time')
  })

  it('draws the tape’s quiet sound scaled to fit, with each scene marked', async () => {
    const { user } = openApp('/sources/5')
    const sound = await screen.findByRole('slider', { name: 'Sound of the tape' })
    await waitFor(() => expect(sound).toHaveAccessibleDescription(/3 chapters, shown \d+(\.\d)?× louder/))
    sound.focus()
    await user.keyboard('{End}{ArrowLeft}') // seeks without playing, to 5 s before the end: the last scene
    expect(within(screen.getByRole('list', { name: 'Chapters' })).getByRole('listitem', { name: 'Scene 6' })).toHaveAttribute(
      'aria-current',
      'time',
    )
    // the player reports its time as it plays: the scene it is in is marked
    const video = document.querySelector('video')!
    Object.defineProperty(video, 'paused', { configurable: true, value: false }) // jsdom plays nothing
    video.currentTime = 2500
    video.dispatchEvent(new Event('timeupdate'))
    video.dispatchEvent(new Event('play'))
    video.dispatchEvent(new Event('playing'))
    await waitFor(() =>
      expect(within(screen.getByRole('list', { name: 'Chapters' })).getByRole('listitem', { name: 'Scene 4' })).toHaveAttribute(
        'aria-current',
        'time',
      ),
    )
    expect(within(screen.getByRole('list', { name: 'Marks' })).getByText('Chapter 2: Christmas dinner, 40:12')).toBeInTheDocument()
  })

  it('shows the transcript by scene in its own tab, in the URL', async () => {
    const { user, router } = openApp('/sources/5')
    await screen.findByRole('list', { name: 'Chapters' })
    await user.click(screen.getByRole('tab', { name: 'Transcript' }))
    expect(router.state.location.search).toContain('view=transcript')
    expect(await screen.findByText('Scene 4')).toBeInTheDocument()
    expect(screen.getByRole('grid', { name: 'Transcript of the tape' })).toHaveTextContent('sprouts')
    // a word plays from where it is said: its phrase becomes the one heard
    await user.click(screen.getByRole('button', { name: /^sprouts/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: /^sprouts/ }).closest('[aria-current]')).not.toBeNull())
  })

  it('says the transcript is still to come', async () => {
    server.use(transcriptWaiting)
    openApp('/sources/5?view=transcript')
    expect(await screen.findByText('No transcript yet')).toBeInTheDocument()
    expect(screen.getByText('Listening for speech (the transcript step).')).toBeInTheDocument()
  })

  it('names what the scenes wait for, shows the steps, and links to the jobs', async () => {
    server.use(scenesWaiting)
    const { user, router } = openApp('/sources/5')
    expect(await screen.findByText('No scenes yet')).toBeInTheDocument()
    expect(screen.getByText(/Grouping the shots into scenes/)).toBeInTheDocument()
    expect(await screen.findByRole('list', { name: 'Steps of the analysis' })).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'jobs page' }))
    expect(router.state.location.pathname).toBe('/jobs')
  })

  it('fills in as the tape’s job runs, without a reload', { timeout: 15_000 }, async () => {
    let grouped = false
    server.use(http.get('*/api/sources/:id/scenes', () => HttpResponse.json(grouped ? tapeScenes : waitingScenes(5))))
    mock.runs[5] = tapeRunPartWay
    openApp('/sources/5')
    expect(await screen.findByText('No scenes yet')).toBeInTheDocument()
    expect(await screen.findByText('Analysing')).toBeInTheDocument() // the job's word, over the steps
    // the scenes are grouped, and the tape's job finishes the rest
    grouped = true
    mock.runs[5] = {
      ...tapeRunPartWay,
      stages: tapeRunPartWay.stages.map(s => ({ ...s, state: 'done' })),
      state: 'done',
      job: { ...tapeRunPartWay.job!, state: 'done' },
    }
    expect(await screen.findByRole('list', { name: 'Chapters' }, { timeout: 12_000 })).toBeInTheDocument()
  })

  it('names a failed step in the finding, and runs the tape again from its Steps tab', async () => {
    mock.runs[5] = tapeRunFailed
    const { user, router } = openApp('/sources/5')
    expect(await screen.findByText(/"Find the faces" failed: the Steps tab says why\.$/)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Steps' }))
    expect(router.state.location.search).toBe('?view=steps')
    const steps = await screen.findByRole('list', { name: 'Steps of the analysis' })
    expect(within(steps).getAllByRole('listitem')).toHaveLength(13) // twelve steps, then the person's check
    const faces = within(steps)
      .getAllByRole('listitem')
      .find(li => li.textContent?.includes('Find the faces'))!
    expect(faces).toHaveTextContent('strata-models is not answering')
    await user.click(within(faces).getByRole('button', { name: /Try again/ }))
    await waitFor(() => expect(mock.runs[5].job?.state).toBe('queued'))
    expect(await screen.findByText('Analysing')).toBeInTheDocument() // the job's progress while it runs again
    expect(screen.getByText('Trying the failed step again shortly')).toBeInTheDocument() // the same job, retrying
    expect(screen.queryByText(/failed: the Steps tab says why/)).toBeNull()
  })

  it('says the steps could not be read without losing the scenes', async () => {
    server.use(runError)
    const { user } = openApp('/sources/5?view=steps')
    expect(await screen.findByRole('alert')).toHaveTextContent('The server could not answer (503)')
    await user.click(screen.getByRole('tab', { name: 'Scenes' }))
    expect(screen.getByRole('list', { name: 'Chapters' })).toBeInTheDocument()
  })

  it('shows a tape part way through its jobs without calling anything missing', async () => {
    server.use(scenesPartWay)
    openApp('/sources/5')
    expect(
      await screen.findByText(
        /^6\sscenes over 2:59:48; not described yet; 2 starts to check; speech still to come; chapters still to come\.$/,
      ),
    ).toBeInTheDocument()
    expect(screen.getAllByText('Not described yet.')).toHaveLength(tapeScenes.scenes.length)
    expect(screen.getByText('The browser copy is still being made')).toBeInTheDocument()
    expect(screen.queryByRole('slider', { name: 'Sound of the tape' })).toBeNull()
    expect(screen.getByRole('img', { name: 'Frames of scene 1: No picture' })).toBeInTheDocument()
    // not grouped into chapters yet: the scenes are one list
    expect(screen.queryByRole('list', { name: 'Chapters' })).toBeNull()
    expect(within(screen.getByRole('list', { name: 'Scenes' })).getAllByRole('listitem')).toHaveLength(6)
  })

  it('says the server could not answer', async () => {
    server.use(scenesError)
    openApp('/sources/5')
    expect(await screen.findByRole('alert')).toHaveTextContent('The server could not answer (503)')
  })

  describe('corrections', () => {
    const headings = (list: HTMLElement) =>
      within(list)
        .getAllByRole('listitem', { name: /^Chapter \d/ })
        .map(c => within(c).getAllByRole('heading')[0].textContent)

    it('asks the person to confirm the chapters, with the view’s one filled button, and says when they did', async () => {
      const { user } = openApp('/sources/5')
      const confirm = await screen.findByRole('button', { name: 'Confirm the chapters' })
      expectOneFilledButton()
      await user.click(confirm)
      expect(await screen.findByText(/^Chapters confirmed /)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Confirm the chapters' })).toBeNull()
      expect(mock.runs[5].stages.at(-1)).toMatchObject({ name: 'check_chapters', state: 'done' })
    })

    it('confirms the chapters from the Steps tab too', async () => {
      const { user } = openApp('/sources/5?view=steps')
      const steps = await screen.findByRole('list', { name: 'Steps of the analysis' })
      const check = within(steps)
        .getAllByRole('listitem')
        .find(li => li.textContent?.includes('Check the chapters'))!
      await user.click(within(check).getByRole('button', { name: /Approve/ }))
      await waitFor(() => expect(mock.runs[5].state).toBe('done'))
    })

    it('starts a chapter at a scene, shows the mark, and gives it back to the grouping', async () => {
      const { user } = openApp('/sources/5')
      const list = await screen.findByRole('list', { name: 'Chapters' })
      // the first scene of a chapter is joined from its chapter, so only the scenes inside one offer a start
      expect(within(list).queryByRole('button', { name: 'Start a chapter at scene 1' })).toBeNull()
      expect(within(list).queryByRole('button', { name: 'Start a chapter at scene 4' })).toBeNull()
      await user.click(within(list).getByRole('button', { name: 'Start a chapter at scene 3' }))
      await waitFor(() => expect(headings(list)).toHaveLength(4))
      expect(headings(list)[1]).toBe('Chapter 2') // a new chapter: named once the job has run
      await user.click(within(list).getByRole('button', { name: 'Rename chapter 2' }))
      expect(await screen.findByRole('textbox', { name: 'Title of chapter 2' })).toHaveValue('') // nothing to start from
      await user.keyboard('{Escape}')
      const third = within(list).getByRole('listitem', { name: 'Scene 3' })
      expect(within(third).getByText('You started a chapter here')).toBeInTheDocument()
      expect(mock.marks).toEqual({ 2: true })
      await user.click(within(third).getByRole('button', { name: 'Undo your mark on scene 3' }))
      await waitFor(() => expect(headings(list)).toHaveLength(3))
      expect(mock.marks).toEqual({})
    })

    it('joins a chapter to the one before', async () => {
      const { user } = openApp('/sources/5')
      const list = await screen.findByRole('list', { name: 'Chapters' })
      expect(within(list).queryByRole('button', { name: /^Join chapter 1/ })).toBeNull()
      await user.click(within(list).getByRole('button', { name: 'Join chapter 3 to chapter 2' }))
      await waitFor(() => expect(headings(list)).toEqual(['Chapter 1: Christmas morning', 'Chapter 2']))
      expect(within(list).getByText('You kept this with the chapter before')).toBeInTheDocument()
    })

    it('gives a chapter the person’s own title, keeps the namer’s in sight, and gives it back', async () => {
      const { user } = openApp('/sources/5')
      const list = await screen.findByRole('list', { name: 'Chapters' })
      await user.click(within(list).getByRole('button', { name: 'Rename chapter 2' }))
      const field = await screen.findByRole('textbox', { name: 'Title of chapter 2' })
      expect(field).toHaveValue('Christmas dinner')
      await user.clear(field)
      await user.click(screen.getByRole('button', { name: 'Save title' }))
      expect(field).toHaveAccessibleDescription(/Give the chapter a title of a word or more\./)
      await user.type(field, 'Dinner at Grandma’s')
      await user.click(screen.getByRole('button', { name: 'Save title' }))
      await waitFor(() => expect(headings(list)[1]).toBe('Chapter 2: Dinner at Grandma’s'))
      expect(screen.queryByRole('textbox', { name: 'Title of chapter 2' })).toBeNull() // saved: closed
      expect(within(list).getByText('Your title. The namer called it “Christmas dinner”.')).toBeInTheDocument()
      await user.click(within(list).getByRole('button', { name: 'Rename chapter 2' }))
      await user.click(await screen.findByRole('button', { name: 'Use “Christmas dinner”' }))
      await waitFor(() => expect(headings(list)[1]).toBe('Chapter 2: Christmas dinner'))
    })

    it('renames a chapter not named yet, shows a refused title under its field, and closes on Escape', async () => {
      server.use(
        http.put('*/api/sources/:id/chapters/:chapter/title', () => HttpResponse.json({ detail: 'that title is taken' }, { status: 400 })),
      )
      const unnamed = structuredClone(tapeScenes)
      unnamed.chapters![1].name = null
      unnamed.chapters![1].title = 'Tea'
      server.use(http.get('*/api/sources/:id/scenes', () => HttpResponse.json(unnamed)))
      const { user } = openApp('/sources/5')
      const list = await screen.findByRole('list', { name: 'Chapters' })
      await user.click(within(list).getByRole('button', { name: 'Rename chapter 3' }))
      const field = await screen.findByRole('textbox', { name: 'Title of chapter 3' })
      expect(field).toHaveValue('The evening in front of the television')
      await user.type(field, ' and the film')
      await user.click(screen.getByRole('button', { name: 'Save title' }))
      await waitFor(() => expect(field).toHaveAccessibleDescription(/that title is taken/))
      await user.keyboard('{Escape}')
      await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Title of chapter 3' })).toBeNull())
      // a title of the person's on a chapter the namer has not named: nothing to go back to
      await user.click(within(list).getByRole('button', { name: 'Rename chapter 2' }))
      expect(await screen.findByRole('textbox', { name: 'Title of chapter 2' })).toHaveValue('Tea')
      expect(screen.queryByRole('button', { name: /^Use “/ })).toBeNull()
    })

    it('says a refused correction in the server’s words', async () => {
      server.use(http.put('*/api/sources/:id/scenes/:scene/chapter-mark', () => HttpResponse.json({ detail: 'no' }, { status: 400 })))
      const { user } = openApp('/sources/5')
      const list = await screen.findByRole('list', { name: 'Chapters' })
      await user.click(within(list).getByRole('button', { name: 'Start a chapter at scene 2' }))
      expect(await within(list).findByRole('button', { name: 'Try again' })).toBeInTheDocument()
    })

    it('says the chapters are being grouped again', async () => {
      server.use(http.get('*/api/sources/:id/scenes', () => HttpResponse.json({ ...tapeScenes, regrouping: true })))
      openApp('/sources/5')
      expect(await screen.findByText('Grouping the chapters again by your marks')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Confirm the chapters' })).toBeNull()
    })
  })
})
