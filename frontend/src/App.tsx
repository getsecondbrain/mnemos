import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuthContext } from "./contexts/AuthContext";
import Login from "./components/Login";
import Layout from "./components/Layout";
import ErrorBoundary from "./components/ErrorBoundary";
import Capture from "./components/Capture";
import Timeline from "./components/Timeline";
import MemoryDetail from "./components/MemoryDetail";
import Chat from "./components/Chat";
import Search from "./components/Search";
import Graph from "./components/Graph";
import Heartbeat from "./components/Heartbeat";
import Testament from "./components/Testament";
import Settings from "./components/Settings";
import People from "./components/People";

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
      <Routes>
        <Route element={<Layout onLogout={auth.logout} />}>
          <Route index element={<Navigate to="/timeline" replace />} />
          <Route path="/capture" element={<Capture />} />
          <Route path="/timeline" element={<Timeline />} />
          <Route path="/memory/:id" element={<MemoryDetail />} />
          <Route path="/people" element={<People />} />
          <Route path="/search" element={<Search />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/graph" element={<Graph />} />
          <Route path="/heartbeat" element={<Heartbeat />} />
          <Route path="/testament" element={<Testament />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
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
