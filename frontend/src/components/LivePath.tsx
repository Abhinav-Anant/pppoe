import type { Live } from "../api/types";
import { fmtMbps, fmtNum } from "./ui";

/** Seconds per dash cycle: faster with more traffic, still readable at 10 Gbit/s. */
const dur = (mbps: number | null | undefined) =>
  !mbps || mbps < 0.01 ? null : Math.max(0.35, 4 - Math.log10(1 + mbps) * 0.9);

function Flow({ d, mbps, color, reverse = false, width = 2.5 }: { d: string; mbps: number | null | undefined; color: string; reverse?: boolean; width?: number }) {
  const s = dur(mbps);
  return (
    <g>
      <path d={d} fill="none" stroke={color} strokeOpacity={0.18} strokeWidth={width + 1.5} vectorEffect="non-scaling-stroke" />
      <path d={d} fill="none" stroke={color} strokeWidth={width} vectorEffect="non-scaling-stroke"
        className={`flow ${reverse ? "rev" : ""} ${s == null ? "idle" : ""}`} style={s ? { ["--dur" as string]: `${s}s` } : undefined} />
    </g>
  );
}

/**
 * Subscribers -> gateway -> internet, drawn with live numbers. Downstream (optic) flows toward the
 * subscribers, upstream (amber) toward the internet; animation speed follows the measured subscriber
 * throughput (what a BNG carries to and from the internet). The uplink NIC's own counters are shown
 * separately in the footer: they also carry management traffic, and on some layouts access VLANs.
 */
export default function LivePath({ live }: { live: Live }) {
  const s = live.sessions;
  const up = live.nics[live.uplink];
  const strands = [34, 62, 118, 146];
  return (
    <section className="surface relative overflow-hidden rounded-2xl border border-zinc-800" aria-label="Live traffic path">
      <div className="relative h-[230px]">
        <svg viewBox="0 0 1000 230" preserveAspectRatio="none" className="absolute inset-0 h-full w-full" aria-hidden="true">
          {strands.map((y) => (
            <Flow key={y} d={`M250 ${y} C360 ${y} 390 90 470 90`} mbps={s.download_mbps} color="var(--optic)" reverse />
          ))}
          <Flow d="M250 90 H470" mbps={s.upload_mbps} color="var(--amber)" />
          <Flow d="M530 82 H750" mbps={s.download_mbps} color="var(--optic)" reverse />
          <Flow d="M530 98 H750" mbps={s.upload_mbps} color="var(--amber)" />
        </svg>
        <div className="absolute left-1/2 top-[58px] flex h-16 w-16 -translate-x-1/2 items-center justify-center rounded-full border border-zinc-700 bg-zinc-950">
          <span className={`h-5 w-5 rounded-full ${live.accel_active ? "bg-sky-500" : "bg-red-500"}`} />
        </div>

        <div className="absolute inset-y-0 left-0 flex w-[25%] flex-col justify-center pl-7">
          <div className="text-xs text-zinc-500">Subscribers online</div>
          <div className="num text-5xl font-semibold tracking-tight text-zinc-100">{fmtNum(s.active)}</div>
          <div className="num mt-1 text-xs text-zinc-500">{fmtNum(s.logins_per_min)} logins in the last minute</div>
        </div>

        <div className="absolute left-1/2 top-[134px] w-60 -translate-x-1/2 text-center">
          <div className="text-sm font-semibold text-zinc-100">{live.node}</div>
          <div className="num text-xs text-zinc-500">
            {live.accel_active ? `CPU ${live.host.cpu_percent ?? "—"}%, ${fmtNum(live.conntrack.count)} tracked flows` : "Gateway daemon stopped"}
          </div>
        </div>

        <div className="absolute inset-y-0 right-0 flex w-[25%] flex-col items-end justify-center pr-7 text-right">
          <div className="text-xs text-zinc-500">Subscriber traffic</div>
          <div className="num text-3xl font-semibold tracking-tight text-zinc-100">{fmtMbps(s.download_mbps)}</div>
          <div className="num text-xs text-zinc-500">down, {fmtMbps(s.upload_mbps)} up</div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-zinc-800 px-7 py-2.5 text-xs text-zinc-500">
        <span className="flex items-center gap-2"><i className="h-0.5 w-5 rounded bg-[var(--optic)]" />Download</span>
        <span className="flex items-center gap-2"><i className="h-0.5 w-5 rounded bg-[var(--amber)]" />Upload</span>
        <span className="num ml-auto">Uplink {live.uplink}: {fmtMbps(up?.rx_mbps)} in, {fmtMbps(up?.tx_mbps)} out, {fmtNum(up?.rx_pps)} packets/s</span>
      </div>
    </section>
  );
}
