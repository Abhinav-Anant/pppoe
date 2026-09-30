import { useState } from "react";
import { api } from "../api/client";
import type { Me } from "../api/types";
import { useBranding } from "../components/brand";
import { Mark } from "../components/Layout";
import { Button, ErrorNote, inputCls } from "../components/ui";

export default function Login({ onLogin }: { onLogin: (m: Me) => void }) {
  const brand = useBranding();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      onLogin(await api<Me>("/api/auth/login", { json: { username, password }, headers: { "X-Requested-With": "bng" } }));
    } catch (err) {
      setError((err as Error).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden overflow-hidden bg-zinc-100 p-12 text-zinc-950 lg:flex lg:flex-col lg:justify-between">
        <div className="flex items-center gap-3">
          <Mark className="h-9 w-9" />
          <span className="text-lg font-semibold tracking-tight">{brand.name}</span>
        </div>
        <svg viewBox="0 0 600 220" className="w-full max-w-xl" aria-hidden="true">
          {[40, 80, 120, 160].map((y, i) => (
            <g key={y}>
              <path d={`M0 ${y} C160 ${y} 200 110 300 110`} fill="none" stroke="var(--optic)" strokeOpacity=".25" strokeWidth="4" />
              <path className="flow rev" style={{ ["--dur" as string]: `${2.4 + i * 0.5}s` }} d={`M0 ${y} C160 ${y} 200 110 300 110`}
                fill="none" stroke="var(--optic)" strokeWidth="3" />
            </g>
          ))}
          <path d="M300 110 H600" fill="none" stroke="var(--amber)" strokeOpacity=".25" strokeWidth="4" />
          <path className="flow" style={{ ["--dur" as string]: "1.8s" }} d="M300 110 H600" fill="none" stroke="var(--amber)" strokeWidth="3" />
          <circle cx="300" cy="110" r="16" fill="var(--z950)" />
          <circle cx="300" cy="110" r="7" fill="var(--optic)" />
        </svg>
        <div className="max-w-md">
          <p className="text-3xl font-semibold leading-tight tracking-tight">Every subscriber, every plan, one gateway.</p>
          <p className="mt-3 text-sm text-zinc-600">PPPoE sessions, per-plan shaping, carrier-grade NAT and RADIUS, operated from one console.</p>
        </div>
      </section>
      <section className="flex items-center justify-center p-8">
        <form onSubmit={submit} className="w-full max-w-sm space-y-4">
          <div className="mb-6 flex items-center gap-3 lg:hidden">
            <Mark className="h-9 w-9" />
            <span className="text-lg font-semibold tracking-tight text-zinc-100">{brand.name}</span>
          </div>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-zinc-100">Sign in</h1>
            <p className="mt-1 text-sm text-zinc-500">Use your administrator account for this gateway.</p>
          </div>
          <label className="block text-xs font-medium text-zinc-400">
            Username
            <input className={`${inputCls} mt-1.5 w-full py-2 text-sm`} autoComplete="username" value={username}
              onChange={(e) => setUsername(e.target.value)} autoFocus />
          </label>
          <label className="block text-xs font-medium text-zinc-400">
            Password
            <input className={`${inputCls} mt-1.5 w-full py-2 text-sm`} type="password" autoComplete="current-password"
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <ErrorNote error={error} />
          <Button variant="primary" className="w-full py-2 text-sm" disabled={busy || !username || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </section>
    </div>
  );
}
