import { NavLink, Outlet } from "react-router-dom";

const navItems = [
  { to: "/capture", label: "Capture", icon: "+" },
  { to: "/timeline", label: "Timeline", icon: "\u2630" },
  { to: "/search", label: "Search", icon: "\u2315" },
  { to: "/chat", label: "Chat", icon: "\uD83D\uDCAC" },
  { to: "/graph", label: "Graph", icon: "\u25C9" },
  { to: "/heartbeat", label: "Heartbeat", icon: "\u2665" },
  { to: "/testament", label: "Testament", icon: "\uD83D\uDD11" },
  { to: "/settings", label: "Settings", icon: "\u2699" },
];

export default function Layout({ onLogout }: { onLogout: () => Promise<void> }) {
  return (
    <div className="flex h-screen">
      <nav className="w-56 bg-gray-900 border-r border-gray-800 flex flex-col">
        <div className="p-4 border-b border-gray-800">
          <h1 className="text-xl font-bold tracking-tight">Mnemos</h1>
        </div>
        <ul className="flex-1 py-2 space-y-1 overflow-y-auto">
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
        <div className="p-4 border-t border-gray-800">
          <button
            onClick={onLogout}
            className="w-full text-left text-sm text-gray-400 hover:text-gray-200 transition-colors"
          >
            Lock & Logout
          </button>
        </div>
      </nav>
      <main className="flex-1 overflow-y-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
