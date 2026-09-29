import { useState } from "react";
import type { AuditEntry } from "../api/types";
import { Badge, Empty, ErrorNote, fmtTime, inputCls, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

export default function Audit() {
  const [src, setSrc] = useState<"db" | "node">("db");
  const [action, setAction] = useState("");
  const db = useApi<AuditEntry[]>(src === "db" ? `/api/audit?limit=500${action ? `&action=${action}` : ""}` : null, 15000);
  const node = useApi<Record<string, unknown>[]>(src === "node" ? "/api/audit/node?limit=500" : null, 15000);
  const tone = (r: string) => (r === "ok" || r === "applied" ? "ok" : r === "failed" || r === "rolled_back" || r === "rejected" || r === "throttled" ? "bad" : "dim");

  return (
    <>
      <PageHeader title="Audit">
        <select className={inputCls} value={src} onChange={(e) => setSrc(e.target.value as "db" | "node")}>
          <option value="db">Management (logins, API actions)</option>
          <option value="node">Node (CLI + API config changes)</option>
        </select>
        {src === "db" && (
          <select className={inputCls} value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">all actions</option>
            {["login", "logout", "session_disconnect", "config_apply", "config_rollback", "user_create", "user_update", "password_change"].map((a) => <option key={a}>{a}</option>)}
          </select>
        )}
      </PageHeader>
      <ErrorNote error={db.error ?? node.error} />
      <div className="overflow-auto rounded border border-zinc-800" style={{ maxHeight: "calc(100vh - 120px)" }}>
        {src === "db" ? (
          <table className={T.table}>
            <thead><tr>{["Time", "Admin", "Source IP", "Action", "Target", "Result", "Detail"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
            <tbody>
              {db.data?.map((a) => (
                <tr key={a.id} className={T.tr}>
                  <td className={T.td}>{fmtTime(a.timestamp)}</td><td className={T.td}>{a.admin ?? "—"}</td>
                  <td className={`${T.td} font-mono`}>{a.source_ip ?? "—"}</td><td className={T.td}>{a.action}</td>
                  <td className={`${T.td} font-mono`}>{a.target ?? ""}</td>
                  <td className={T.td}><Badge tone={tone(a.result)}>{a.result}</Badge></td>
                  <td className={`${T.td} whitespace-normal font-mono text-[11px] text-zinc-500`}>{a.detail ? JSON.stringify(a.detail) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <table className={T.table}>
            <thead><tr>{["Time", "Admin", "Source", "Action", "Result", "Detail"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr></thead>
            <tbody>
              {node.data?.map((a, i) => {
                const { timestamp, admin, source, action: act, result, component: _c, diff: _d, ...rest } = a as Record<string, string>;
                return (
                  <tr key={i} className={T.tr}>
                    <td className={T.td}>{fmtTime(timestamp)}</td><td className={T.td}>{admin}</td>
                    <td className={`${T.td} font-mono`}>{source}</td><td className={T.td}>{act}</td>
                    <td className={T.td}><Badge tone={tone(result)}>{result}</Badge></td>
                    <td className={`${T.td} whitespace-normal font-mono text-[11px] text-zinc-500`}>{JSON.stringify(rest)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {(src === "db" ? db.data : node.data)?.length === 0 && <Empty>No entries</Empty>}
      </div>
    </>
  );
}
