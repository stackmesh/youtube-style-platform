# Stage 08C — Final deployment acceptance

Date: 2026-11-14

Verdict: **NOT ACCEPTED**

Gate 1 stopped the run. This host is the development workstation. It has no disposable Ubuntu guest, no passwordless root, and no DNS name that can receive a publicly trusted certificate. Later gates were not run. Local Compose, the development database, and the existing `youtube-prod` and `youtube-e2e` volumes were left untouched.

## 1. Environment

Commands were run on the workstation that holds this repository.

| Check | Result |
| --- | --- |
| OS | Ubuntu 22.04.5 LTS, kernel `6.8.0-138-generic` x86_64 |
| CPU | 16 threads, Intel Core i7-10700K |
| RAM | 31 GiB total, about 5.1 GiB available, swap 2.0 GiB fully used |
| Disk | `/dev/sda2` ext4, 439G size, 246G available |
| Virtualization of this host | `systemd-detect-virt` printed `none` |
| Docker | server and client 29.9.3 |
| Compose | v5.7.0 |
| sudo | `sudo -n true` failed: `sudo: a password is required` |
| Account | `alex`, groups include `sudo`, `docker`, and `lxd`. The account is not in `kvm`. |
| LAN address | `10.1.102.11/24` on `eno1`, default route via `10.1.102.1` |
| Outbound address seen by `https://api.ipify.org` | `107.150.38.250`, routed via `tun0` (`198.18.64.10`) |
| Host ports | `0.0.0.0:80` is listening. `127.0.0.1/` returned HTTP 200. Nothing is listening on 443 or 22. |
| UFW | `ufw status` failed: `You need to be root to run this script` |

The development stack is running on this host. `youtube-clone-edge-1` publishes `0.0.0.0:80->80/tcp` and has been up for 9 hours. `youtube-clone-api-1`, `youtube-clone-worker-1`, `youtube-clone-frontend-1`, `youtube-clone-postgres-1`, `youtube-clone-redis-1`, and `youtube-clone-seaweedfs-1` are up. These volumes already exist:

- `youtube-clone_pg_data`, `youtube-clone_redis_data`, `youtube-clone_seaweed_data`, `youtube-clone_work_data`
- `youtube-prod_pg_data`, `youtube-prod_redis_data`, `youtube-prod_seaweed_data`, `youtube-prod_work_data`, `youtube-prod_caddy_data`, `youtube-prod_caddy_config`
- `youtube-e2e_pg_data`, `youtube-e2e_redis_data`, `youtube-e2e_seaweed_data`, `youtube-e2e_work_data`, `youtube-e2e_caddy_data`, `youtube-e2e_caddy_config`

`youtube-prod-*` and `youtube-e2e-*` containers are stopped. No volume was removed.

Hypervisor tools on the host:

- Present: VMware (`/usr/bin/vmrun`, `/usr/bin/vmware`), `/dev/kvm` readable and writable by this user, Docker.
- Absent from `PATH`: `multipass`, `lxc`, `incus`, `qemu-system-x86_64`, `virt-install`, `virsh`, `VBoxManage`, `virt-manager`, `gnome-boxes`, `limactl`, `colima`.
- `vmrun list` printed `Total running VMs: 0`.
- No `*.vmx` or `*.iso` file was found under the home directory at depth 3. No `/var/lib/libvirt/images` directory exists.

The documented installer target is a clean Ubuntu 22.04 or 24.04 machine with root or passwordless sudo, at least 2 GiB RAM, and 20 GiB free. TLS mode requires `PUBLIC_DOMAIN` and `ACME_EMAIL`, with DNS for the site and `s3.<domain>` pointed at that machine before certificates are requested. `DEMO_HTTP=1` is a local HTTP demo and is not TLS acceptance.

## 2. Gate results

| Gate | Result |
| --- | --- |
| 1. Preflight and clean target | **BLOCKED** |
| 2. Fresh installation | **NOT RUN** |
| 3. Repeat-install and persistence | **NOT RUN** |
| 4. Real HTTPS and security | **NOT RUN** |
| 5. Real application workflow | **NOT RUN** |
| 6. Backup and isolated restore | **NOT RUN** |
| 7. Dependency and image security | **NOT RUN** |
| 8. Regression and final evidence | **NOT RUN** |

## 3. Commands and results

Gate 1 commands, from the repository host:

```text
. /etc/os-release && uname -srvm
nproc && lscpu && free -h && df -hT /
sudo -n true
id
systemd-detect-virt
docker version --format 'server={{.Server.Version}} client={{.Client.Version}}'
docker compose version
docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
docker volume ls --format '{{.Name}}'
ss -ltn
hostname -I && ip -4 addr show scope global && ip route
curl -4 -fsS --max-time 12 https://api.ipify.org
ip route get 107.150.38.250
curl -4 -fsS --max-time 8 -o /dev/null -w '%{http_code}' http://127.0.0.1/
ufw status
vmrun list
```

Observed results:

- OS and kernel match the table in section 1.
- `sudo -n true` exited 1 with `sudo: a password is required`.
- `systemd-detect-virt` printed `none`.
- Docker client and server are 29.9.3. Compose is v5.7.0.
- Port 80 is owned by the development edge container. Ports 22 and 443 are not listening.
- `http://127.0.0.1/` returned 200.
- `ufw status` exited with `You need to be root to run this script`.
- `vmrun list` exited 0 and printed `Total running VMs: 0`.
- The address returned by ipify is reached through `tun0`, while the host default route stays on the private LAN.

No installer, certificate request, backup, restore, image scan, npm audit, or test suite was run in this stage.

## 4. TLS and browser evidence

No certificate was requested. No browser session was opened against a public hostname. HTTPS acceptance is not claimed.

## 5. Upload, transcode, and playback evidence

Not run. No test account was created and no media was uploaded in this stage.

## 6. Private authorization test matrix

Not run.

The known residual from Stage 08B.2 remains documented there: a presigned segment URL issued before revocation or hide can stay usable until its expiry, measured at HTTP 200 inside the 120-second TTL. This stage did not re-measure that residual.

## 7. Backup and isolated restore evidence

Not run. `deploy/backup.sh` and `deploy/restore-rehearsal.sh` were not executed. Existing volumes were not mounted for a dump.

## 8. Container and npm audit results

Not run. The 2026-11-13 scan record in `docs/security/2026-11-13-container-baseline.md` and the Stage 08B.2 edge scan are historical. They are not a current scan of a clean installation.

## 9. Remaining vulnerabilities and residual risks

No new scan was collected. Unresolved items already recorded in `docs/DEPLOYMENT.md` stay in force until Gate 7 scans the images that a clean install actually runs:

- API and worker Bookworm packages with no newer security candidate, including `zlib1g` and `libsqlite3-0`.
- PostgreSQL image `gosu` built with Go 1.24.6 (CVE-2025-68121).
- Caddy 2.11.9: `go-jose/v3` CVE-2026-116587 has no fixed version; `golang.org/x/net` v0.60.0 is behind 0.62.0.
- SeaweedFS 4.53 vendors `golang.org/x/net` v0.61.0, behind 0.62.0.
- Frontend npm audit from Stage 08B.2 still reported critical and high findings in the Vitest/tinypool/Vite test toolchain and a moderate React Router 6.30.6 advisory. The application lockfile was not changed. That audit was not repeated here.

Release impact: none of these were re-checked on a clean server, so they cannot be closed or waived by this stage.

## 10. Changes made

- Added `docs/acceptance/STAGE_08C_FINAL_DEPLOYMENT_ACCEPTANCE.md`.

No installer, Compose, application, or secret file was edited. No volume was deleted. No `.env` was written.

## 11. Recommended commit message

```text
docs: record the blocked clean-server acceptance gate

Stage 08C stopped because this workstation is the development host and has no disposable Ubuntu guest or public DNS name.
```

Do not treat this commit as production acceptance.

## Prerequisites required before the stage can continue

Provide a target that is separate from this workstation:

1. A disposable Ubuntu 22.04 or 24.04 VM or VPS, x86_64 or aarch64, with at least 2 GiB RAM and 20 GiB free disk. It must not already contain `/opt/youtube-clone`, a Test-YouTube `.env`, or Docker volumes named `youtube-clone_*`, `youtube-prod_*`, or `youtube-e2e_*`.
2. Root, or sudo that works without a password prompt (`sudo -n true` exits 0).
3. A public DNS name. Both `<domain>` and `s3.<domain>` must resolve to that machine's reachable address before the installer runs. Also provide the ACME contact email.
4. Inbound TCP 22, 80, and 443 from the internet to that machine. Let's Encrypt must be able to complete the documented challenge. A private LAN address, `localhost`, a self-signed certificate, or `DEMO_HTTP=1` does not satisfy Gate 4.
5. An SSH user and key for that machine. I will run `bash deploy/ubuntu-up.sh` there and will not point it at the development stack on this host.

VMware Workstation is installed here, and `/dev/kvm` is usable, but no guest and no Ubuntu ISO are present. A NAT-only guest on `vmnet8` would still need a public address and DNS. Creating that guest needs the ISO plus the DNS and inbound path above.
