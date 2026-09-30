import { NavLink, Outlet } from "react-router-dom";
import { useBranding, useTheme } from "./brand";
import { useAuth, useLive, useNode } from "./context";
import { Badge, Dot } from "./ui";

type Item = [to: string, label: string, perm?: string];
const NAV: [group: string, items: Item[]][] = [
  ["Operate", [["/dashboard", "Dashboard"], ["/monitoring", "Monitoring"], ["/sessions", "Subscribers", "view_sessions"],
    ["/bng", "All gateways"]]],
  ["Service", [["/qos", "Plans & shaping"], ["/radius", "RADIUS"], ["/nat", "NAT"], ["/pppoe", "PPPoE & pools"],
    ["/interfaces", "Interfaces"], ["/firewall", "Firewall", "manage_firewall"]]],
  ["Platform", [["/configuration", "Configuration"], ["/audit", "Audit log", "view_logs"], ["/users", "Administrators", "manage_users"],
    ["/system", "System"], ["/benchmark", "Performance"]]],
];

/** Two strands meeting in one node: the access side and the uplink joined by the gateway. */
export function Mark({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="var(--z100)" />
      <path d="M5 11c6 0 7 5 11 5M5 21c6 0 7-5 11-5" fill="none" stroke="var(--optic)" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M16 16h11" fill="none" stroke="var(--amber)" strokeWidth="2.2" strokeLinecap="round" />
      <circle cx="16" cy="16" r="3.2" fill="var(--z900)" stroke="var(--z900)" strokeWidth="1" />
    </svg>
  );
}

export default function Layout() {
  const { me, logout } = useAuth();
  const { live, connected, checks } = useLive();
  const { nodes, node, select } = useNode();
  const brand = useBranding();
  const [theme, toggle] = useTheme();
  const fails = (checks ?? []).filter((c) => c.status === "FAIL");
  const up = connected && live?.accel_active;
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-56 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900">
        <div className="flex items-center gap-2.5 px-4 pb-3 pt-4">
          <Mark />
          <div className="min-w-0">
            <div className="truncate text-[15px] font-semibold leading-tight tracking-tight text-zinc-100">{brand.name}</div>
            <div className="truncate text-[11px] text-zinc-500">{brand.tagline}</div>
          </div>
        </div>
        <label className="mx-3 mb-2 flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-2.5 py-1.5">
          <Dot tone={connected ? (live?.accel_active ? "ok" : "bad") : "dim"} />
          <span className="sr-only">Gateway</span>
          <select className="min-w-0 flex-1 bg-transparent text-xs text-zinc-200 outline-none" value={node?.name}
            onChange={(e) => select(e.target.value)}>
            {nodes.map((n) => <option key={n.name} value={n.name}>{n.name}{n.local ? " (this gateway)" : ""}</option>)}
          </select>
        </label>
        <div className="mx-4 mb-3 text-[11px] text-zinc-500">
          {!connected ? "Connecting…" : up ? "Forwarding traffic" : "Gateway daemon stopped"}
        </div>
        <nav className="flex-1 space-y-4 overflow-y-auto px-2 pb-3">
          {NAV.map(([group, items]) => {
            const shown = items.filter(([, , perm]) => !perm || me.permissions.includes(perm));
            return shown.length ? (
              <div key={group}>
                <div className="px-2 pb-1 text-[11px] font-medium text-zinc-500">{group}</div>
                {shown.map(([to, label]) => (
                  <NavLink key={to} to={to} className={({ isActive }) =>
                    `block rounded-lg px-2 py-1.5 text-[13px] ${isActive ? "bg-zinc-950 font-medium text-zinc-100 shadow-[inset_2px_0_0_var(--optic)]"
                      : "text-zinc-400 hover:bg-zinc-950 hover:text-zinc-100"}`}>
                    {label}
                  </NavLink>
                ))}
              </div>
            ) : null;
          })}
        </nav>
        <div className="border-t border-zinc-800 px-4 py-3 text-xs">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate font-medium text-zinc-200">{me.username}</div>
              <div className="text-[11px] text-zinc-500">{me.role.replace("_", " ")}</div>
            </div>
            <button onClick={toggle} title={`Switch to ${theme === "day" ? "night" : "day"} theme`}
              className="rounded-lg border border-zinc-800 px-2 py-1 text-[11px] text-zinc-400 hover:text-zinc-100">
              {theme === "day" ? "Night" : "Day"}
            </button>
          </div>
          <button onClick={logout} className="mt-2 text-[11px] text-zinc-500 hover:text-zinc-100">Sign out</button>
        </div>
      </aside>
      <main className="min-w-0 flex-1">
        {(!connected || fails.length > 0) && (
          <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 bg-zinc-900 px-6 py-2 text-xs">
            {!connected && <Badge tone="warn">Live updates disconnected, reconnecting</Badge>}
            {fails.map((c) => (
              <Badge key={c.name} tone="bad">{c.name}: {c.detail || "failing"}</Badge>
            ))}
          </div>
        )}
        <div className="mx-auto max-w-[1600px] px-6 py-5">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
