# Installation

## Requirements

- Ubuntu 24.04 or Debian 12+ (x86_64), root, Internet access for packages and the ACCEL-PPP source.
- Kernel modules: `pppoe`, `pppox`, `ppp_generic`, `sch_cake`, `ifb`, `act_mirred`, `sch_htb`, `cls_u32`,
  `nf_conntrack`, `nf_nat`, `nft_nat`, `8021q` (checked by the installer).

## Install

From a workstation with the repo (Git Bash / Linux), copy the tree to the node:

```bash
scripts/deploy.sh bng01
```

On the node:

```bash
sudo bash /opt/bng-platform/src/scripts/install.sh
```

The installer prints OS/CPU/RAM/NICs/kernel, checks modules, installs build dependencies,
builds ACCEL-PPP **1.14.0** from the upstream tag into `/usr/local`, installs
`accel-ppp.service` (enabled, not started), installs `bngctl` into `/opt/bng-platform/venv`,
creates `/etc/bng-platform/config.yaml` (lab mode, uplink = default-route interface) only if it
does not exist, and adds an `include` for `/etc/bng-platform/nftables/*.nft` to
`/etc/nftables.conf` (backup kept). It **never edits netplan or NIC configuration**.
Re-running it is safe.

## First start (lab)

```bash
sudo bash /opt/bng-platform/src/scripts/lab/lab-up.sh   # veth bnglab0 <-> netns bnglab
sudo bngctl config apply                                # starts accel-ppp, health-gated
sudo bngctl firewall apply                              # reverts in 120 s unless confirmed
# from a NEW ssh session within 120 s:
sudo bngctl firewall confirm
sudo bash /opt/bng-platform/src/scripts/lab/lab-test.sh # end-to-end PPPoE test
sudo /opt/bng-platform/src/scripts/health-check.sh
```

## Uninstall

```bash
sudo bash /opt/bng-platform/src/scripts/uninstall.sh          # asks; keeps /etc and /var/lib data
sudo bash /opt/bng-platform/src/scripts/uninstall.sh --yes --purge
```

Stopping accel-ppp disconnects every subscriber.

## Proxmox notes

- **VirtIO** NICs are fine for functional testing.
- Put the PPPoE access NIC on an **isolated bridge** (no physical port, or a dedicated access VLAN).
  Never run PPPoE on a NIC that shares a provider LAN: the BNG would answer every PADI there.
- Enable **multiqueue** per vNIC (`queues=N`, N ≤ vCPUs), then inside the VM
  `ethtool -L <nic> combined N`. Without it all receive processing lands on one CPU.
- CPU type `host`, no CPU overcommit on the hypervisor while benchmarking.
- For real throughput tests use PCIe passthrough or SR-IOV (machine type **q35**).

Virtual-NIC benchmark results do not indicate what the software can do on physical 10/25/40G
NICs; the virtio/vhost path is the bottleneck. No 40G claim is valid from a VM result.
