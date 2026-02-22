import { useState, useEffect, useCallback } from "react";
import { NavLink, Outlet, useLocation, useOutletContext } from "react-router-dom";
import Logo from "./Logo";
import FilterPanel, { useFilterTags, useFilterPersons, useFilterSearchParams } from "./FilterPanel";
import type { FilterState, TagData, PersonData } from "./FilterPanel";
import { useEncryption } from "../hooks/useEncryption";
import SearchOverlay from "./SearchOverlay";

export interface LayoutOutletContext {
  filters: FilterState;
  setFilters: (fs: FilterState) => void;
  clearAllFilters: () => void;
  removeContentType: (ct: string) => void;
  removeDateRange: () => void;
  removeTagId: (tagId: string) => void;
  removePersonId: (personId: string) => void;
  resetVisibility: () => void;
  removeLocation: () => void;
  tagData: TagData;
  personData: PersonData;
}

export function useLayoutFilters(): LayoutOutletContext {
  return useOutletContext<LayoutOutletContext>();
}

const navItems: { to: string; label: string; icon: React.ReactNode }[] = [
  {
    to: "/people",
    label: "People",
    icon: (
      <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a9.094 9.094 0 003.741-.479 3 3 0 00-4.682-2.72m.94 3.198l.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0112 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 016 18.719m12 0a5.971 5.971 0 00-.941-3.197m0 0A5.995 5.995 0 0012 12.75a5.995 5.995 0 00-5.058 2.772m0 0a3 3 0 00-4.681 2.72 8.986 8.986 0 003.74.477m.94-3.197a5.971 5.971 0 00-.94 3.197M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z" />
      </svg>
    ),
  },
  {
    to: "/map",
    label: "Map",
    icon: (
      <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 10.5a3 3 0 11-6 0 3 3 0 016 0z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 10.5c0 7.142-7.5 11.25-7.5 11.25S4.5 17.642 4.5 10.5a7.5 7.5 0 1115 0z" />
      </svg>
    ),
  },
  {
    to: "/chat",
    label: "Chat",
    icon: (
      <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095 48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155" />
      </svg>
    ),
  },
  {
    to: "/graph",
    label: "Graph",
    icon: (
      <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
        <circle cx="5" cy="12" r="2.5" />
        <circle cx="19" cy="6" r="2.5" />
        <circle cx="19" cy="18" r="2.5" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 11L16.5 7M7.5 13L16.5 17" />
      </svg>
    ),
  },
  {
    to: "/heartbeat",
    label: "Heartbeat",
    icon: (
      <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 8.25c0-2.485-2.099-4.5-4.688-4.5-1.935 0-3.597 1.126-4.312 2.733-.715-1.607-2.377-2.733-4.313-2.733C5.1 3.75 3 5.765 3 8.25c0 7.22 9 12 9 12s9-4.78 9-12z" />
      </svg>
    ),
  },
  {
    to: "/testament",
    label: "Testament",
    icon: (
      <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
      </svg>
    ),
  },
  {
    to: "/settings",
    label: "Settings",
    icon: (
      <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
];

export default function Layout({ onLogout }: { onLogout: () => Promise<void> }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const { filters, setFilters, clearAllFilters, removeContentType, removeDateRange, removeTagId, removePersonId, resetVisibility, removeLocation } = useFilterSearchParams();
  const tagData = useFilterTags();
  const personData = useFilterPersons();
  const location = useLocation();
  const { isUnlocked } = useEncryption();

  const closeSearch = useCallback(() => setSearchOpen(false), []);

  // Close mobile menu on route change
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  // Cmd+K / Ctrl+K to open search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setSearchOpen((prev) => !prev);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  return (
    <div className="flex flex-col md:flex-row h-screen">
      {/* Mobile top bar */}
      <div className="md:hidden flex items-center justify-between bg-gray-900 border-b border-gray-800 px-4 py-3">
        <div className="flex items-center gap-3">
          <Logo />
          <div className={`flex items-center gap-1 text-xs ${isUnlocked ? "text-emerald-400" : "text-red-400"}`} role="status" aria-live="polite">
            {isUnlocked ? (
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 11V7a4 4 0 118 0m-4 8v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2z" />
              </svg>
            ) : (
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
            )}
            <span>{isUnlocked ? "Unlocked" : "Locked"}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setSearchOpen(true)}
            className="p-1.5 text-gray-400 hover:text-gray-200 transition-colors rounded-lg hover:bg-gray-800"
            title="Search (⌘K)"
            aria-label="Open search"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
            </svg>
          </button>
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
      </div>

      {/* Sidebar nav -- hidden on mobile unless menuOpen */}
      <nav className={`${menuOpen ? "flex" : "hidden"} md:flex w-full md:w-56 bg-gray-900 border-b md:border-b-0 md:border-r border-gray-800 flex-col`}>
        <div className="hidden md:block p-4 border-b border-gray-800">
          <div className="flex items-center justify-between">
            <Logo />
            <button
              onClick={() => setSearchOpen(true)}
              className="p-1.5 text-gray-400 hover:text-gray-200 transition-colors rounded-lg hover:bg-gray-800"
              title="Search (⌘K)"
              aria-label="Open search"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
              </svg>
            </button>
          </div>
          <div className={`flex items-center gap-1.5 text-xs mt-1.5 ${isUnlocked ? "text-emerald-400" : "text-red-400"}`} role="status" aria-live="polite">
            {isUnlocked ? (
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 11V7a4 4 0 118 0m-4 8v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2z" />
              </svg>
            ) : (
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
            )}
            <span>{isUnlocked ? "Unlocked" : "Locked"}</span>
          </div>
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
                {item.icon}
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

      <main className={`flex-1 ${location.pathname === "/graph" ? "relative overflow-hidden" : "overflow-y-auto p-4 md:p-6"}`}>
        <Outlet context={{ filters, setFilters, clearAllFilters, removeContentType, removeDateRange, removeTagId, removePersonId, resetVisibility, removeLocation, tagData, personData } satisfies LayoutOutletContext} />
      </main>

      <SearchOverlay open={searchOpen} onClose={closeSearch} />
    </div>
  );
}
