import { useCallback, useEffect, useState } from "react";
import { get } from "../api/client";

// GET a path; `reload()` refetches. `every` (ms) polls while mounted.
export function useApi<T>(path: string | null, every?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      setData(await get<T>(path));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    reload();
    if (!every) return;
    const t = window.setInterval(reload, every);
    return () => window.clearInterval(t);
  }, [reload, every]);

  return { data, error, loading, reload, setData };
}
