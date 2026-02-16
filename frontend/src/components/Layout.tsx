import { useState, useEffect } from "react";
import { NavLink, Outlet, useLocation, useOutletContext } from "react-router-dom";
import Logo from "./Logo";
import FilterPanel, { useFilterTags, useFilterPersons, useFilterSearchParams } from "./FilterPanel";
import type { FilterState, TagData, PersonData } from "./FilterPanel";

export interface LayoutOutletContext {
  filters: FilterState;
  setFilters: (fs: FilterState) => void;
  clearAllFilters: () => void;
  removeContentType: (ct: string) => void;
  removeDateRange: () => void;
  removeTagId: (tagId: string) => void;
  removePersonId: (personId: string) => void;
  resetVisibility: () => void;
  tagData: TagData;
  personData: PersonData;
}

export function useLayoutFilters(): LayoutOutletContext {
  return useOutletContext<LayoutOutletContext>();
}

const navItems = [
  { to: "/capture", label: "Capture", icon: "+" },
  { to: "/people", label: "People", icon: "\u{1F464}" },
  { to: "/search", label: "Search", icon: "\u2315" },
  { to: "/chat", label: "Chat", icon: "\uD83D\uDCAC" },
  { to: "/graph", label: "Graph", icon: "\u25C9" },
  { to: "/heartbeat", label: "Heartbeat", icon: "\u2665" },
  { to: "/testament", label: "Testament", icon: "\uD83D\uDD11" },
  { to: "/settings", label: "Settings", icon: "\u2699" },
];

export default function Layout({ onLogout }: { onLogout: () => Promise<void> }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { filters, setFilters, clearAllFilters, removeContentType, removeDateRange, removeTagId, removePersonId, resetVisibility } = useFilterSearchParams();
  const tagData = useFilterTags();
  const personData = useFilterPersons();
  const location = useLocation();

  // Close mobile menu on route change
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  return (
    <div className="flex flex-col md:flex-row h-screen">
      {/* Mobile top bar */}
      <div className="md:hidden flex items-center justify-between bg-gray-900 border-b border-gray-800 px-4 py-3">
        <Logo />
        <button
          onClick={() => setMenuOpen(!menuOpen)}
          className="text-gray-400 hover:text-gray-200 p-1"
          aria-label="Toggle menu"
        >
          {menuOpen ? (
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : (
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          )}
        </button>
      </div>

      {/* Sidebar nav -- hidden on mobile unless menuOpen */}
      <nav className={`${menuOpen ? "flex" : "hidden"} md:flex w-full md:w-56 bg-gray-900 border-b md:border-b-0 md:border-r border-gray-800 flex-col`}>
        <div className="hidden md:block p-4 border-b border-gray-800">
          <Logo />
        </div>
        <ul className="py-2 space-y-1">
          {navItems.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
                    isActive
                      ? "bg-gray-800 text-white"
                      : "text-gray-400 hover:text-gray-200 hover:bg-gray-800/50"
                  }`
                }
              >
                <span className="w-5 text-center">{item.icon}</span>
                <span>{item.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>
        {/* Desktop filter panel */}
        <div className="hidden md:block flex-1 overflow-y-auto border-t border-gray-800">
          <FilterPanel filters={filters} onFilterChange={setFilters} variant="sidebar" tagData={tagData} personData={personData} />
        </div>
        <div className="p-4 border-t border-gray-800">
          <button
            onClick={onLogout}
            className="w-full text-left text-sm text-gray-400 hover:text-gray-200 transition-colors"
          >
            Lock & Logout
          </button>
        </div>
      </nav>

      {/* Mobile filter trigger + sheet */}
      <FilterPanel filters={filters} onFilterChange={setFilters} variant="mobile" tagData={tagData} personData={personData} />

      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <Outlet context={{ filters, setFilters, clearAllFilters, removeContentType, removeDateRange, removeTagId, removePersonId, resetVisibility, tagData, personData } satisfies LayoutOutletContext} />
      </main>
    </div>
  );
}
