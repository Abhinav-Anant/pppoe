import { useLive, useNode } from "../components/context";
import { Badge, Empty, PageHeader } from "../components/ui";

// easywall's own console, served by bng-api at /easywall/ (same origin, behind our login).
export default function Firewall() {
  const { node } = useNode();
  const { checks } = useLive();
  const ew = checks?.find((c) => c.name === "easywall");
  return (
    <>
      <PageHeader title="Firewall — easywall">
        {ew && <Badge tone={ew.status === "PASS" ? "ok" : ew.status === "FAIL" ? "bad" : "dim"}>{ew.status}: {ew.detail}</Badge>}
        {node?.local && <a className="text-xs text-sky-400 hover:underline" href="/easywall/" target="_blank" rel="noopener">open in a new tab</a>}
      </PageHeader>
      {!node?.local ? (
        <Empty>
          The easywall console is embedded for this node only. Open {node?.name}'s own BNG console (its bng-api) to manage its easywall.
        </Empty>
      ) : ew?.status === "SKIP" ? (
        <Empty>easywall is not installed on this node (scripts/easywall-install.sh).</Empty>
      ) : (
        <>
          <div className="mb-2 text-[11px] text-zinc-500">
            Host-input firewall (easywall, routing mode "open"). Subscriber forwarding and NAT stay in bng_filter / bng_nat.
            easywall keeps its own login; every change it applies auto-reverts unless you confirm it.
          </div>
          <iframe title="easywall" src="/easywall/" className="w-full rounded border border-zinc-800 bg-white" style={{ height: "calc(100vh - 130px)" }} />
        </>
      )}
    </>
  );
}
