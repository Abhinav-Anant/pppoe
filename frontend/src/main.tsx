import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import { get, post, setCsrf, setRemoteNode, setUnauthorizedHandler } from "./api/client";
import type { Me } from "./api/types";
import Layout from "./components/Layout";
import { AuthContext, LiveProvider, NodeContext, type NodeInfo } from "./components/context";
import Audit from "./pages/Audit";
import Bng from "./pages/Bng";
import Configuration from "./pages/Configuration";
import Dashboard from "./pages/Dashboard";
import Firewall from "./pages/Firewall";
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
import Benchmark from "./pages/Benchmark";
import Users from "./pages/Users";

const NODE_KEY = "bng.node";
const saved = () => { try { return localStorage.getItem(NODE_KEY); } catch { return null; } };

function Console() {
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(saved());

  const reloadNodes = useCallback(() => { get<NodeInfo[]>("/api/nodes").then(setNodes).catch(() => {}); }, []);
  useEffect(reloadNodes, [reloadNodes]);

  const node = nodes.find((n) => n.name === selected) ?? nodes.find((n) => n.local) ?? null;
  // every API call and socket below goes to the selected node (through this console's proxy)
  setRemoteNode(node && !node.local ? node.name : null);
  const select = (name: string) => {
    try { localStorage.setItem(NODE_KEY, name); } catch { /* private window */ }
    setSelected(name);
  };

  if (!node) return <div className="p-6 text-xs text-zinc-500">Loading…</div>;
  return (
    <NodeContext.Provider value={{ nodes, node, select, reloadNodes }}>
      {/* key: switching node remounts everything, so no state leaks between BNGs */}
      <LiveProvider key={node.name}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/bng" element={<Bng />} />
            <Route path="/bng/:id" element={<Bng />} />
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
            <Route path="/firewall" element={<Firewall />} />
            <Route path="/configuration" element={<Configuration />} />
            <Route path="/configuration/history" element={<Configuration tab="history" />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/users" element={<Users />} />
            <Route path="/system" element={<System />} />
            <Route path="/benchmark" element={<Benchmark />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Route>
        </Routes>
      </LiveProvider>
    </NodeContext.Provider>
  );
}

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
      <Console />
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
