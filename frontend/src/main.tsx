import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import { get, post, setCsrf, setUnauthorizedHandler } from "./api/client";
import type { Me } from "./api/types";
import Layout from "./components/Layout";
import { AuthContext, LiveProvider } from "./components/context";
import Audit from "./pages/Audit";
import Configuration from "./pages/Configuration";
import Dashboard from "./pages/Dashboard";
import Interfaces from "./pages/Interfaces";
import Login from "./pages/Login";
import Monitoring from "./pages/Monitoring";
import Nat from "./pages/Nat";
import Pppoe from "./pages/Pppoe";
import Qos from "./pages/Qos";
import Radius from "./pages/Radius";
import SessionDetail from "./pages/SessionDetail";
import Sessions from "./pages/Sessions";
import System from "./pages/System";
import Users from "./pages/Users";

function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  useEffect(() => {
    setUnauthorizedHandler(() => setMe(null));
    get<Me>("/api/auth/me")
      .then((m) => (setCsrf(m.csrf_token), setMe(m)))
      .catch(() => setMe(null));
  }, []);

  if (me === undefined) return <div className="p-6 text-xs text-zinc-500">Loading…</div>;
  if (me === null) return <Login onLogin={(m) => (setCsrf(m.csrf_token), setMe(m))} />;

  const logout = () => post("/api/auth/logout").finally(() => setMe(null));
  return (
    <AuthContext.Provider value={{ me, logout }}>
      <LiveProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/monitoring" element={<Monitoring />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/sessions/:sid" element={<SessionDetail />} />
            <Route path="/qos" element={<Qos />} />
            <Route path="/nat" element={<Nat />} />
            <Route path="/radius" element={<Radius />} />
            <Route path="/pppoe" element={<Pppoe />} />
            <Route path="/ip-pools" element={<Navigate to="/pppoe" replace />} />
            <Route path="/interfaces" element={<Interfaces />} />
            <Route path="/configuration" element={<Configuration />} />
            <Route path="/configuration/history" element={<Configuration tab="history" />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/users" element={<Users />} />
            <Route path="/system" element={<System />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Route>
        </Routes>
      </LiveProvider>
    </AuthContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
