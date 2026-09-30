import { NavLink, Outlet } from "react-router-dom";
import { useAuth, useLive, useNode } from "./context";
import { Badge, Dot } from "./ui";

const NAV: [string, string, string?][] = [
  ["/bng", "All BNGs"],
  ["/dashboard", "Dashboard"],
  ["/monitoring", "Monitoring"],
  ["/sessions", "Sessions", "view_sessions"],
  ["/qos", "QoS / Plans"],
  ["/nat", "NAT"],
  ["/radius", "RADIUS"],
  ["/pppoe", "PPPoE & Pools"],
  ["/interfaces", "Interfaces"],
  ["/firewall", "Firewall", "manage_firewall"],
  ["/configuration", "Configuration"],
  ["/audit", "Audit", "view_logs"],
  ["/users", "Users", "manage_users"],
  ["/system", "System"],
  ["/benchmark", "Benchmark"],
];

export default function Layout() {
  const { me, logout } = useAuth();
  const { live, connected, checks } = useLive();
  const { nodes, node, select } = useNode();
  const fails = (checks ?? []).filter((c) => c.status === "FAIL");
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-44 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950">
        <div className="border-b border-zinc-800 px-3 py-2.5">
          <div className="text-sm font-semibold tracking-wide text-zinc-100">BNG Console</div>
          <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-zinc-500">
            <Dot tone={connected ? (live?.accel_active ? "ok" : "bad") : "dim"} />
            <select
              aria-label="BNG node"
              className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 text-[11px] text-zinc-200"
              value={node?.name}
              onChange={(e) => select(e.target.value)}
            >
              {nodes.map((n) => <option key={n.name} value={n.name}>{n.name}{n.local ? " (this node)" : ""}</option>)}
            </select>
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto py-1">
          {NAV.filter(([, , perm]) => !perm || me.permissions.includes(perm)).map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `block border-l-2 px-3 py-1.5 text-xs ${isActive ? "border-sky-500 bg-zinc-900 text-zinc-100" : "border-transparent text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"}`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-zinc-800 px-3 py-2 text-[11px] text-zinc-500">
          <div className="truncate text-zinc-300">{me.username}</div>
          <div className="flex items-center justify-between">
            <span>{me.role}</span>
            <button onClick={logout} className="text-zinc-400 hover:text-zinc-100">Log out</button>
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1">
        {(!connected || fails.length > 0) && (
          <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 bg-zinc-900 px-4 py-1.5 text-xs">
            {!connected && <Badge tone="warn">live updates disconnected — reconnecting</Badge>}
            {fails.map((c) => (
              <Badge key={c.name} tone="bad">{c.name}: {c.detail || "FAIL"}</Badge>
            ))}
          </div>
        )}
        <div className="p-4">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
