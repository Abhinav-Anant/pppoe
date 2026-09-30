import { useEffect, useState } from "react";

// ---- theme: "day" (default, for presenting) or "night" (NOC); remembered per browser -------------
export type Theme = "day" | "night";
const KEY = "bng.theme";

export function initialTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    if (t === "day" || t === "night") return t;
  } catch { /* storage blocked: fall through */ }
  return "day";
}

export function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem(KEY, t); } catch { /* not persisted, still applied */ }
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useEffect(() => applyTheme(theme), [theme]);
  return [theme, () => setTheme((t) => (t === "day" ? "night" : "day"))];
}

/** Read a theme token (e.g. "--optic") for SVG/canvas code that cannot use classes. */
export const token = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// ---- branding: set by the operator in /etc/bng-platform/branding.json ----------------------------
export interface Branding { name: string; tagline: string }
let cached: Promise<Branding> | null = null;
const fallback: Branding = { name: "BNG Console", tagline: "Broadband network gateway" };

export function useBranding(): Branding {
  const [b, setB] = useState<Branding>(fallback);
  useEffect(() => {
    cached ??= fetch("/api/branding").then((r) => (r.ok ? r.json() : fallback)).catch(() => fallback);
    cached.then((v) => { setB(v); document.title = v.name; });
  }, []);
  return b;
}
