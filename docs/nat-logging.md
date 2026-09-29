# NAT logging design (CGNAT correlation)

Status: **design only** — not deployed. The data plane must never depend on it.

## What must be recorded

For every translated flow: timestamp (start, end), subscriber/private IP, private source
port, public IP, public source port, protocol. To answer "who was 43.229.72.90:40123 at
time T", the record must be joined with the PPPoE session (username ↔ private IP over time),
which accel-ppp already sends to Jaze as accounting (Framed-IP-Address, start/stop).

## Source: conntrack events

Linux conntrack emits NEW and DESTROY events over netlink carrying the original tuple
(private src/sport) and the reply tuple (public dst = our SNAT address/port). With
`net.netfilter.nf_conntrack_timestamp = 1` DESTROY events include start/stop times.

```text
conntrack (kernel) --netlink NFCT events--> bng-natlog (optional service) --> sink
```

`bng-natlog` options, in order of preference:

1. **ulogd2** with the NFCT input plugin → IPFIX (NAT event template) to an external collector.
2. `conntrack -E -e NEW,DESTROY -o timestamp,extended` parsed by a small service → JSON lines
   over syslog-TLS or HTTP to ClickHouse / a logging server.

## Rules

- Never PostgreSQL. Volume is roughly flows/s × record size; 30k subscribers can produce
  tens of thousands of events per second. PostgreSQL holds management state only.
- Non-blocking: bounded in-memory queue, drop-with-counter when the sink is slow; expose the
  drop counter to monitoring. Netlink socket buffer sized so the kernel does not stall.
- If `bng-natlog` or the sink is down, forwarding continues (conntrack does not wait for
  listeners). Missing logs are an alert, not an outage.
- Retention and access are the sink's job (legal retention period, access audit).

## Scaling alternative: port-block allocation

Per-flow logs are expensive at scale. With port-block allocation (PBA) each subscriber is
assigned a fixed public IP + port block for the session, and only the assignment is logged
(one record per session instead of per flow). nftables `snat` has no native PBA; options
are deterministic NAT (static per-subscriber ranges via generated rules/maps) or a dedicated
CGNAT engine. Decide with measured flow rates in Phase 8; the current `persistent` SNAT keeps
one public IP per subscriber, which already simplifies correlation.
