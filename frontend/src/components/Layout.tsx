import { NavLink, Outlet } from "react-router-dom";
import { useAuth, useLive } from "./context";
import { Badge, Dot } from "./ui";

const NAV: [string, string, string?][] = [
  ["/dashboard", "Dashboard"],
  ["/monitoring", "Monitoring"],
  ["/sessions", "Sessions", "view_sessions"],
  ["/qos", "QoS / Plans"],
  ["/nat", "NAT"],
  ["/radius", "RADIUS"],
  ["/pppoe", "PPPoE & Pools"],
  ["/interfaces", "Interfaces"],
  ["/configuration", "Configuration"],
  ["/audit", "Audit", "view_logs"],
  ["/users", "Users", "manage_users"],
  ["/system", "System"],
];

export default function Layout() {
  const { me, logout } = useAuth();
  const { live, connected, checks } = useLive();
  const fails = (checks ?? []).filter((c) => c.status === "FAIL");
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-44 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950">
        <div className="border-b border-zinc-800 px-3 py-2.5">
          <div className="text-sm font-semibold tracking-wide text-zinc-100">BNG Console</div>
          <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-zinc-500">
            <Dot tone={connected ? (live?.accel_active ? "ok" : "bad") : "dim"} />
            {live?.node ?? "…"}
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
