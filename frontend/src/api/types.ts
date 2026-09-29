// Shapes returned by bng-api (backend/app/api/main.py).

export type Status = "PASS" | "FAIL" | "SKIP";
export interface Check { name: string; status: Status; detail: string }

export interface Me { username: string; role: string; permissions: string[]; csrf_token: string }

export interface Session {
  sid: string; username: string; ip: string | null; ip6: string | null; ip6_delegated: string | null;
  mac: string; called_sid: string; ifname: string; inbound_if: string | null; vlan: number | null;
  state: "start" | "active" | "finish" | string; uptime_s: number | null;
  upload_bytes: number | null; download_bytes: number | null;
  upload_packets: number | null; download_packets: number | null;
  upload_mbps: number | null; download_mbps: number | null;
  peak_upload_mbps: number; peak_download_mbps: number;
  rate_limit: string | null; rate_down_kbit: number | null; rate_up_kbit: number | null;
  duplicate: boolean;
}
export interface Page<T> { total: number; page: number; page_size: number; items: T[] }

export interface NicRates {
  rx_mbps: number | null; tx_mbps: number | null; rx_pps: number | null; tx_pps: number | null;
  errors: number; drops: number; state: string; speed_mbps: number | null;
}

export interface Live {
  timestamp: number; node: string; uplink: string; accel_active: boolean; accel_error: string | null;
  accel: { uptime?: string; cpu?: string; "mem(rss/virt)"?: string; sessions?: Record<string, string>; pppoe?: Record<string, string> };
  sessions: {
    total: number; active: number; duplicates: number; unshaped: number;
    download_mbps: number; upload_mbps: number; logins_per_min: number; logouts_per_min: number;
  };
  host: {
    hostname: string; uptime_s: number | null; load: number[] | null; mem_total_bytes: number | null;
    mem_available_bytes: number | null; cpus: number | null; cpu_percent: number | null; softirq_percent: number | null;
  };
  nics: Record<string, NicRates>;
  conntrack: { count: number | null; max: number | null };
  nat: { pool: string; packets: number; bytes: number }[];
}

export interface Nic {
  name: string; mac: string; state: string; mtu: number; speed_mbps: number | null; duplex: string | null;
  rx_queues: number; tx_queues: number; rx_bytes: number; tx_bytes: number; rx_packets: number; tx_packets: number;
  rx_errors: number; tx_errors: number; rx_dropped: number; tx_dropped: number;
}

export interface Version { version: number; timestamp: string; admin: string; source: string; restart?: boolean }

export interface AuditEntry {
  id: number; timestamp: string; admin: string | null; source_ip: string | null; action: string;
  target: string | null; result: string; detail: Record<string, unknown> | null;
}

export interface User { username: string; role: string; disabled: boolean; created_at: string; last_login_at: string | null }

export const ROLES = ["super_admin", "network_admin", "noc_operator", "read_only"] as const;
