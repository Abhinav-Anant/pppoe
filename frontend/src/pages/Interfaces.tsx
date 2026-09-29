import type { Nic } from "../api/types";
import { useLive } from "../components/context";
import { Empty, ErrorNote, fmtBytes, fmtMbps, fmtNum, PageHeader, T } from "../components/ui";
import { useApi } from "../hooks/useApi";

export default function Interfaces() {
  const { data, error } = useApi<Nic[]>("/api/interfaces", 10000);
  const { live } = useLive();
  return (
    <>
      <PageHeader title="Interfaces" />
      <ErrorNote error={error} />
      <div className="overflow-auto rounded border border-zinc-800">
        <table className={T.table}>
          <thead>
            <tr>{["Name", "State", "MAC", "MTU", "Speed", "Queues rx/tx", "Rx now", "Tx now", "Rx bytes", "Tx bytes", "Errors", "Drops"].map((h) => <th key={h} className={T.th}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {data?.map((n) => {
              const r = live?.nics[n.name];
              const err = n.rx_errors + n.tx_errors, drop = n.rx_dropped + n.tx_dropped;
              return (
                <tr key={n.name} className={T.tr}>
                  <td className={T.td}>{n.name}{n.name === live?.uplink && <span className="ml-1 text-[10px] text-sky-400">uplink</span>}</td>
                  <td className={`${T.td} ${n.state === "up" ? "text-emerald-400" : n.state === "down" ? "text-red-400" : "text-zinc-500"}`}>{n.state}</td>
                  <td className={`${T.td} font-mono`}>{n.mac}</td>
                  <td className={`${T.td} num`}>{n.mtu}</td>
                  <td className={`${T.td} num`}>{n.speed_mbps ? `${n.speed_mbps} Mb` : "—"}</td>
                  <td className={`${T.td} num ${n.rx_queues === 1 && n.name === live?.uplink ? "text-amber-400" : ""}`}>{n.rx_queues}/{n.tx_queues}</td>
                  <td className={`${T.td} num`}>{fmtMbps(r?.rx_mbps)}</td>
                  <td className={`${T.td} num`}>{fmtMbps(r?.tx_mbps)}</td>
                  <td className={`${T.td} num`}>{fmtBytes(n.rx_bytes)}</td>
                  <td className={`${T.td} num`}>{fmtBytes(n.tx_bytes)}</td>
                  <td className={`${T.td} num ${err ? "text-amber-400" : ""}`}>{fmtNum(err)}</td>
                  <td className={`${T.td} num ${drop ? "text-amber-400" : ""}`}>{fmtNum(drop)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!data && <Empty>Loading…</Empty>}
      </div>
      <p className="mt-2 text-[11px] text-zinc-500">A single RX queue on the uplink puts all receive processing on one CPU (see Proxmox multiqueue).</p>
    </>
  );
}
