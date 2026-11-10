import { Menu, Search } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

export function AppShell() {
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-screen bg-ink text-white">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-ink px-3 md:px-4">
        <button
          type="button"
          className="rounded-full p-2 hover:bg-panel md:hidden"
          aria-label="Open navigation"
          onClick={() => setOpen(true)}
        >
          <Menu size={22} />
        </button>
        <NavLink to="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent">
            <span className="ml-0.5 h-0 w-0 border-y-[6px] border-l-[10px] border-y-transparent border-l-white" />
          </span>
          Playback
        </NavLink>
        <form className="mx-auto hidden w-full max-w-xl md:block" role="search">
          <label className="relative block">
            <span className="sr-only">Search</span>
            <Search className="pointer-events-none absolute left-3 top-2.5 text-muted" size={18} />
            <input
              disabled
              placeholder="Search is not available yet"
              className="w-full rounded-full border border-line bg-panel py-2 pl-10 pr-4 text-sm text-muted"
            />
          </label>
        </form>
      </header>
      <div className="flex">
        <aside className="hidden w-56 shrink-0 border-r border-line p-3 md:block">
          <SidebarLink onNavigate={() => undefined} />
        </aside>
        {open ? (
          <button
            type="button"
            aria-label="Close navigation"
            className="fixed inset-0 z-30 bg-black/60 md:hidden"
            onClick={() => setOpen(false)}
          />
        ) : null}
        <aside
          className={`fixed inset-y-0 left-0 z-40 w-64 border-r border-line bg-ink p-3 transition md:hidden ${
            open ? "translate-x-0" : "-translate-x-full"
          }`}
        >
          <SidebarLink onNavigate={() => setOpen(false)} />
        </aside>
        <main className="min-w-0 flex-1 p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function SidebarLink({ onNavigate }: { onNavigate: () => void }) {
  return (
    <nav aria-label="Primary">
      <NavLink
        to="/"
        onClick={onNavigate}
        className={({ isActive }) =>
          `block rounded-lg px-3 py-2 text-sm ${isActive ? "bg-panel font-medium" : "hover:bg-panel"}`
        }
      >
        Home
      </NavLink>
    </nav>
  );
}
