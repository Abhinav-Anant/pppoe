import type { ReactNode } from "react";
import type { Status } from "../api/types";

// ---- formatting --------------------------------------------------------------
export const fmtBytes = (n: number | null | undefined) => {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  let v = n;
  while (v >= 1000 && i < u.length - 1) (v /= 1000), i++;
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
};
export const fmtMbps = (n: number | null | undefined) =>
  n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(2)} Gbps` : `${n.toFixed(n < 10 ? 2 : 1)} Mbps`;
export const fmtNum = (n: number | null | undefined, d = 0) =>
  n == null ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: d });
export const fmtDur = (s: number | null | undefined) => {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`;
};
export const fmtKbit = (k: number | null | undefined) =>
  k == null ? "—" : k >= 1000 ? `${fmtNum(k / 1000, 1)}M` : `${k}k`;
export const fmtTime = (iso: string | number | null | undefined) =>
  iso == null ? "—" : new Date(typeof iso === "number" ? iso * 1000 : iso).toLocaleString();

// ---- primitives --------------------------------------------------------------
const tones = {
  ok: "text-emerald-400", bad: "text-red-400", warn: "text-amber-400", dim: "text-zinc-500", info: "text-sky-400",
};
export type Tone = keyof typeof tones;
export const statusTone = (s: Status | string): Tone =>
  s === "PASS" ? "ok" : s === "FAIL" ? "bad" : s === "SKIP" ? "dim" : "warn";

export function Dot({ tone }: { tone: Tone }) {
  const bg = { ok: "bg-emerald-400", bad: "bg-red-500", warn: "bg-amber-400", dim: "bg-zinc-600", info: "bg-sky-400" }[tone];
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${bg}`} />;
}

export function Badge({ tone = "dim", children }: { tone?: Tone; children: ReactNode }) {
  const b = {
    ok: "border-emerald-700/60 bg-emerald-950/60 text-emerald-300", bad: "border-red-700/60 bg-red-950/60 text-red-300",
    warn: "border-amber-700/60 bg-amber-950/60 text-amber-300", dim: "border-zinc-700 bg-zinc-900 text-zinc-400",
    info: "border-sky-700/60 bg-sky-950/60 text-sky-300",
  }[tone];
  return <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-px text-[11px] font-medium ${b}`}>{children}</span>;
}

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`surface rounded-xl border border-zinc-800 ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-zinc-800 px-4 py-2.5">
          <h2 className="text-[13px] font-semibold text-zinc-100">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone }) {
  return (
    <div className="surface rounded-xl border border-zinc-800 px-4 py-3">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className={`num mt-1 text-xl font-semibold tracking-tight ${tone ? tones[tone] : "text-zinc-100"}`}>{value}</div>
      {sub != null && <div className="num mt-0.5 text-[11px] text-zinc-500">{sub}</div>}
    </div>
  );
}

export function Button({ variant = "default", className = "", ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "danger" | "ghost" }) {
  const v = {
    default: "border-zinc-700 bg-zinc-800 hover:bg-zinc-700 text-zinc-200",
    primary: "border-sky-700 bg-sky-800 hover:bg-sky-700 text-white",
    danger: "border-red-800 bg-red-900/70 hover:bg-red-800 text-red-100",
    ghost: "border-transparent hover:bg-zinc-800 text-zinc-300",
  }[variant];
  return <button {...p} className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${v} ${className}`} />;
}

export const inputCls =
  "rounded-lg border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-100 outline-none focus:border-sky-600";

export function ErrorNote({ error }: { error: string | null | undefined }) {
  return error ? <div className="rounded-lg border border-red-800 bg-red-950 px-3 py-2 text-xs text-red-300">{error}</div> : null;
}

export function Empty({ children = "Nothing to show" }: { children?: ReactNode }) {
  return <div className="px-3 py-6 text-center text-xs text-zinc-500">{children}</div>;
}

export function KV({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-xs">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-zinc-500">{k}</dt>
          <dd className="num break-all text-zinc-200">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

// Tables: shared classes keep every table dense and identical.
export const T = {
  table: "w-full border-collapse text-xs",
  th: "sticky top-0 bg-zinc-900 px-2.5 py-2 text-left text-[11px] font-medium text-zinc-500 border-b border-zinc-800 whitespace-nowrap",
  td: "px-2.5 py-1.5 border-b border-zinc-800/70 whitespace-nowrap",
  tr: "hover:bg-zinc-800/40",
};

export function CheckList({ checks }: { checks: { name: string; status: Status; detail: string }[] }) {
  return (
    <table className={T.table}>
      <tbody>
        {checks.map((c) => (
          <tr key={c.name} className={T.tr}>
            <td className={`${T.td} w-40`}>
              <span className="flex items-center gap-2"><Dot tone={statusTone(c.status)} />{c.name}</span>
            </td>
            <td className={`${T.td} w-14`}><Badge tone={statusTone(c.status)}>{c.status}</Badge></td>
            <td className={`${T.td} whitespace-normal text-zinc-400`}>{c.detail}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
      <h1 className="text-xl font-semibold tracking-tight text-zinc-100">{title}</h1>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}
