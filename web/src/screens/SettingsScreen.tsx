// The one question: "How does the app look, and how do I change it?" The app keeps no settings of
// its own yet (src/app/settings.py), so this is Appearance only: Strata's ColorSchemeChoice, which
// ThemeProvider (main.tsx) keeps in this browser. When the server gains settings, they become a
// General tab beside it, with the tab in the URL (Tabs param="tab").
import { ColorSchemeChoice, PageHeader } from 'strata-react'

export function SettingsScreen() {
  return (
    <>
      <PageHeader title="Settings" scope="Kept in this browser" />
      <div className="grid max-w-xl gap-3">
        <ColorSchemeChoice legend="Colour scheme" />
        <p className="type-body-sm text-muted-foreground">Light, dark, or whichever this computer uses.</p>
      </div>
    </>
  )
}
