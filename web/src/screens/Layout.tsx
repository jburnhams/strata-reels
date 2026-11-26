// The frame every screen shares: product name, side nav (a scrolling row on phones) and the main
// region, from Strata's AppShell. Nav items are React Router NavLinks, so the current screen is
// marked with aria-current. Each screen supplies its own PageHeader inside <Outlet />. Screens are a
// reading width; a route with `handle: { wide: true }` (the tape page, whose player sits beside its
// chapters on a wide screen) takes the width a desktop has.
import { Film, ListChecks, Server, Settings } from 'lucide-react'
import { Outlet, useMatches } from 'react-router'
import { AppShell, type NavItem } from 'strata-react'

export const nav: NavItem[] = [
  { to: '/sources', label: 'Sources', icon: Film },
  { to: '/jobs', label: 'Jobs', icon: ListChecks },
  { to: '/system', label: 'System', icon: Server },
  { to: '/settings', label: 'Settings', icon: Settings },
]

/** The product's name. */
export const product = 'strata-reels'

/** Whether the route shown asked for the full width. */
const isWide = (handle: unknown) => typeof handle === 'object' && handle !== null && 'wide' in handle && handle.wide === true

export function Layout() {
  const wide = useMatches().some(m => isWide(m.handle))
  return (
    <AppShell product={product} nav={nav}>
      <div className={wide ? 'grid max-w-7xl gap-8 pb-16' : 'grid max-w-5xl gap-8 pb-16'}>
        <Outlet />
      </div>
    </AppShell>
  )
}
