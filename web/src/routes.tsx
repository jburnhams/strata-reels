// The app's route tree, in its own module so the app (main.tsx), the jsdom tests (renderRoutes from
// strata-react/testing) and the stories build their routers from the same objects: a test that
// hand-copied the tree would keep passing after the app's routes changed.
//
// Routes are real paths (/system, /settings), not #hashes: the server answers every
// page path with the app (src/app/server/app.py), so a reload or a shared link opens that screen,
// and anything a person would link to (sort, tab, page) lives in the URL's search. The sources list is
// /sources and a tape is /sources/<id> under it, so the menu marks Sources on a tape's page too; the
// app's root sends the person there.
import { Navigate, type RouteObject } from 'react-router'
import { JobsScreen } from './screens/JobsScreen'
import { Layout } from './screens/Layout'
import { NotFoundScreen } from './screens/NotFoundScreen'
import { SettingsScreen } from './screens/SettingsScreen'
import { SourcesScreen } from './screens/SourcesScreen'
import { SystemScreen } from './screens/SystemScreen'
import { TapeScreen } from './screens/TapeScreen'

export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="sources" replace /> },
      { path: 'sources', element: <SourcesScreen /> },
      { path: 'sources/:id', element: <TapeScreen />, handle: { wide: true } },
      { path: 'jobs', element: <JobsScreen /> },
      { path: 'system', element: <SystemScreen /> },
      { path: 'settings', element: <SettingsScreen /> },
      { path: '*', element: <NotFoundScreen /> },
    ],
  },
]
