import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuthContext } from "./contexts/AuthContext";
import Login from "./components/Login";
import Layout from "./components/Layout";
import ErrorBoundary from "./components/ErrorBoundary";
import Timeline from "./components/Timeline";
import MemoryDetail from "./components/MemoryDetail";
import Search from "./components/Search";

function lazyWithRetry(
  factory: () => Promise<{ default: React.ComponentType<unknown> }>,
  retries = 2,
): ReturnType<typeof lazy> {
  return lazy(() => {
    const attempt = (remaining: number): Promise<{ default: React.ComponentType<unknown> }> =>
      factory().catch((err: unknown) => {
        if (remaining <= 0) throw err;
        return new Promise<{ default: React.ComponentType<unknown> }>((resolve) =>
          setTimeout(() => resolve(attempt(remaining - 1)), 1000),
        );
      });
    return attempt(retries);
  });
}

const Chat = lazyWithRetry(() => import("./components/Chat"));
const Graph = lazyWithRetry(() => import("./components/Graph"));
const Heartbeat = lazyWithRetry(() => import("./components/Heartbeat"));
const Testament = lazyWithRetry(() => import("./components/Testament"));
const Settings = lazyWithRetry(() => import("./components/Settings"));
const People = lazyWithRetry(() => import("./components/People"));
const MapView = lazyWithRetry(() => import("./components/MapView"));

function AppRoutes() {
  const auth = useAuthContext();

  if (auth.isLoading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <p className="text-gray-400 text-lg">Loading...</p>
      </div>
    );
  }

  if (!auth.isAuthenticated) {
    return (
      <Login
        setupRequired={auth.setupRequired}
        onSetup={auth.setup}
        onLogin={auth.login}
      />
    );
  }

  return (
    <ErrorBoundary>
      <Suspense fallback={
        <div className="min-h-screen bg-gray-950 flex items-center justify-center">
          <p className="text-gray-400 text-lg">Loading...</p>
        </div>
      }>
      <Routes>
        <Route element={<Layout onLogout={auth.logout} />}>
          <Route index element={<Navigate to="/timeline" replace />} />
          <Route path="/capture" element={<Navigate to="/timeline" replace />} />
          <Route path="/timeline" element={<Timeline />} />
          <Route path="/memory/:id" element={<MemoryDetail />} />
          <Route path="/people" element={<People />} />
          <Route path="/map" element={<MapView />} />
          <Route path="/search" element={<Search />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/graph" element={<Graph />} />
          <Route path="/heartbeat" element={<Heartbeat />} />
          <Route path="/testament" element={<Testament />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}

function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}

export default App;
