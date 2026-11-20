"""
Hardware of the Tinker Board (CPU, temperature, memory, disk, network) and the
systemd services. Everything from /proc and /sys – no extra packages.
"""
from __future__ import annotations

import os
import platform
import random
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def read(p: str, default: str = "") -> str:
    try:
        return Path(p).read_text().strip().strip("\x00")
    except Exception:
        return default


def run(cmd: List[str], timeout: float = 15) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def sudo() -> List[str]:
    return [] if os.geteuid() == 0 else ["sudo", "-n"]


def _cpu_times() -> List[List[int]]:
    out = []
    for line in read("/proc/stat").splitlines():
        if line.startswith("cpu"):
            out.append([int(x) for x in line.split()[1:9]])
    return out


def _net() -> Dict[str, Tuple[int, int]]:
    d = {}
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) >= 9:
            d[name.strip()] = (int(f[0]), int(f[8]))
    return d


def default_iface() -> str:
    for line in read("/proc/net/route").splitlines()[1:]:
        f = line.split()
        if len(f) > 2 and f[1] == "00000000":
            return f[0]
    return ""


class Hardware:
    """Sampled every few seconds by a thread; ``now`` is the latest picture."""

    def __init__(self, demo: bool = False):
        self.demo = demo
        self.iface = "auto"
        self.now: Dict[str, Any] = {}
        self._cpu = _cpu_times()
        self._net = _net()
        self._t = time.time()
        self._proc: Dict[int, Tuple[float, int]] = {}
        self.static = self._static()

    def _static(self) -> Dict[str, Any]:
        os_name = ""
        for line in read("/etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                os_name = line.split("=", 1)[1].strip('"')
        model = read("/proc/device-tree/model") or platform.machine()
        freq_max = read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
        info = {"hostname": socket.gethostname(), "model": model, "os": os_name, "kernel": platform.release(),
                "python": platform.python_version(), "cpus": os.cpu_count(), "arch": platform.machine(),
                "freq_max": int(freq_max) // 1000 if freq_max.isdigit() else None, "dietpi": read("/boot/dietpi/.version")}
        if self.demo:
            info.update({"hostname": "ZelloBridge", "model": "Rockchip RK3288 Asus Tinker Board", "os": "Debian GNU/Linux 12 (bookworm)",
                         "kernel": "6.6.63-current-rockchip", "cpus": 4, "arch": "armv7l", "freq_max": 1800})
        return info

    def _temps(self) -> Dict[str, float]:
        out = {}
        for z in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
            t = read(str(z / "temp"))
            if t.lstrip("-").isdigit():
                out[read(str(z / "type")) or z.name] = round(int(t) / 1000, 1)
        return out

    def proc(self, pid: int, dt: float) -> Dict[str, Any]:
        """CPU % and memory of one process (the bridge)."""
        if not pid:
            return {}
        stat = read(f"/proc/{pid}/stat")
        if not stat:
            return {}
        f = stat.rsplit(")", 1)[1].split()
        ticks = int(f[11]) + int(f[12])
        rss = int(f[21]) * os.sysconf("SC_PAGE_SIZE")
        hz = os.sysconf("SC_CLK_TCK")
        prev = self._proc.get(pid)
        self._proc = {pid: (time.time(), ticks)}
        cpu = None
        if prev and time.time() > prev[0]:
            cpu = round(100 * (ticks - prev[1]) / hz / (time.time() - prev[0]), 1)
        return {"pid": pid, "cpu": cpu, "rss": rss, "threads": int(f[17])}

    def sample(self, bridge_pid: int = 0) -> Dict[str, Any]:
        now = time.time()
        dt = max(0.1, now - self._t)
        cpu_now = _cpu_times()
        usage = []
        for a, b in zip(self._cpu, cpu_now):
            total = sum(b) - sum(a)
            idle = (b[3] + b[4]) - (a[3] + a[4])
            usage.append(round(100 * (total - idle) / total, 1) if total > 0 else 0.0)
        self._cpu = cpu_now
        mem = {}
        for line in read("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                mem[k] = int(v.split()[0]) * 1024
        net_now = _net()
        iface = self.iface if self.iface not in ("", "auto") else default_iface()
        rx = tx = 0.0
        if iface in net_now and iface in self._net:
            rx = max(0.0, (net_now[iface][0] - self._net[iface][0]) / dt)
            tx = max(0.0, (net_now[iface][1] - self._net[iface][1]) / dt)
        self._net, self._t = net_now, now
        du = shutil.disk_usage("/")
        temps = self._temps()
        freq = read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
        try:
            up = float(read("/proc/uptime", "0").split()[0])
        except ValueError:
            up = 0.0
        d = {"ts": now, "cpu": usage[0] if usage else 0.0, "cores": usage[1:], "temp": max(temps.values()) if temps else None,
             "temps": temps, "freq": int(freq) // 1000 if freq.isdigit() else None,
             "mem_total": mem.get("MemTotal"), "mem_used": (mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)),
             "swap_total": mem.get("SwapTotal"), "swap_used": mem.get("SwapTotal", 0) - mem.get("SwapFree", 0),
             "disk_total": du.total, "disk_used": du.total - du.free, "iface": iface, "rx": rx, "tx": tx,
             "rx_total": net_now.get(iface, (0, 0))[0], "tx_total": net_now.get(iface, (0, 0))[1],
             "load": [round(x, 2) for x in os.getloadavg()], "uptime": up, "ip": self._ip(),
             "bridge": self.proc(bridge_pid, dt)}
        if self.demo:
            d = self._demo(d)
        self.now = d
        return d

    @staticmethod
    def _ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return ""

    def _demo(self, d: Dict[str, Any]) -> Dict[str, Any]:
        t = time.time()
        busy = 1 if int(t / 40) % 3 == 0 else 0
        cores = [round(random.uniform(3, 9) + busy * random.uniform(8, 18), 1) for _ in range(4)]
        temp = round(47 + busy * 4 + random.uniform(-0.6, 0.6), 1)
        d.update(cpu=round(sum(cores) / 4, 1), cores=cores, temp=temp, temps={"cpu-thermal": temp, "gpu-thermal": round(temp - 2.1, 1)},
                 freq=1608 if busy else 1008, mem_total=2_090_000_000, mem_used=int(372e6 + random.uniform(0, 8e6)),
                 swap_total=1_073_741_824, swap_used=0, disk_total=31_000_000_000, disk_used=4_420_000_000, iface="eth0",
                 rx=random.uniform(1800, 2600) + busy * 9000, tx=random.uniform(1500, 2200) + busy * 8500,
                 rx_total=8_712_000_000, tx_total=7_903_000_000, load=[round(0.18 + busy * 0.3, 2), 0.21, 0.19],
                 uptime=19 * 86400 + 5 * 3600, ip="192.168.79.7",
                 bridge={"pid": 315, "cpu": round(random.uniform(1.5, 3) + busy * 9, 1), "rss": 33_968_000, "threads": 3})
        return d


class Services:
    """State of the bridge and the further services via systemctl."""

    PROPS = ("Id", "ActiveState", "SubState", "MainPID", "NRestarts", "ActiveEnterTimestamp", "Environment", "MemoryCurrent",
             "ExecMainStartTimestamp", "UnitFileState", "Description", "FragmentPath", "DropInPaths")

    def __init__(self, demo: bool = False):
        self.demo = demo
        self._lock = threading.Lock()

    def show(self, unit: str) -> Dict[str, str]:
        if self.demo:
            return self._demo_show(unit)
        rc, out = run(["systemctl", "show", f"{unit}.service" if "." not in unit else unit, *[f"-p{p}" for p in self.PROPS]], 10)
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    def find_extra(self, setting: str, bridge: str) -> List[str]:
        if setting and setting != "auto":
            return [s.strip() for s in setting.split(",") if s.strip() and s.strip() != bridge]
        if self.demo:
            return ["svxlink"]
        rc, out = run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain"], 10)
        found = []
        for line in out.splitlines():
            name = line.split()[0] if line.split() else ""
            base = name.removesuffix(".service")
            if base != bridge and any(k in base.lower() for k in ("svxlink", "asterisk", "usrp", "analog_bridge", "allmon")):
                found.append(base)
        return found[:6]

    def control(self, unit: str, action: str) -> Tuple[int, str]:
        if action not in ("start", "stop", "restart"):
            raise ValueError("unknown action")
        if self.demo:
            return 0, ""
        return run([*sudo(), "systemctl", action, f"{unit}.service"], 60)

    def journal(self, unit: str, lines: int = 300) -> str:
        if self.demo:
            from .demo import demo_journal
            return demo_journal()
        rc, out = run(["journalctl", "-u", f"{unit}.service", "-n", str(min(int(lines), 3000)), "--no-pager", "-o", "short-iso"], 20)
        return out

    def power(self, action: str) -> Tuple[int, str]:
        if action not in ("reboot", "poweroff"):
            raise ValueError("unknown action")
        if self.demo:
            return 0, ""
        return run([*sudo(), "systemctl", action], 30)

    @staticmethod
    def parse_env(env: str) -> Dict[str, str]:
        """systemd prints Environment= as space separated KEY=VALUE, values with spaces in quotes."""
        out: Dict[str, str] = {}
        i, n = 0, len(env)
        while i < n:
            while i < n and env[i] == " ":
                i += 1
            j, buf, q = i, [], None
            while j < n and (q or env[j] != " "):
                c = env[j]
                if c in "\"'" and (q is None or q == c):
                    q = None if q else c
                elif c == "\\" and j + 1 < n:
                    j += 1
                    buf.append(env[j])
                else:
                    buf.append(c)
                j += 1
            item = "".join(buf)
            if "=" in item:
                k, v = item.split("=", 1)
                out[k] = v
            i = j + 1
        return out

    def _demo_show(self, unit: str) -> Dict[str, str]:
        started = time.strftime("%a %Y-%m-%d %H:%M:%S CET", time.localtime(time.time() - 5 * 3600 - 1260))
        if unit == "svxlink":
            return {"Id": "svxlink.service", "ActiveState": "active", "SubState": "running", "MainPID": "298", "NRestarts": "0",
                    "ActiveEnterTimestamp": started, "Description": "SvxLink repeater control software", "Environment": "",
                    "MemoryCurrent": "14200832", "UnitFileState": "enabled"}
        return {"Id": f"{unit}.service", "ActiveState": "active", "SubState": "running", "MainPID": "315", "NRestarts": "2",
                "ActiveEnterTimestamp": started, "Description": "AllStarLink Zello Bridge", "MemoryCurrent": "35651584",
                "UnitFileState": "enabled", "FragmentPath": "/etc/systemd/system/asl-zello-bridge.service",
                "DropInPaths": "/etc/systemd/system/asl-zello-bridge.service.d/override.conf",
                "Environment": 'USRP_BIND=127.0.0.1 USRP_HOST=127.0.0.1 USRP_RXPORT=34001 USRP_TXPORT=32001 ZELLO_USERNAME=n8schicht-bridge '
                               'ZELLO_PASSWORD=secret "ZELLO_CHANNEL=Die N8schicht" ZELLO_PRIVATE_KEY=/opt/asl-zello-bridge/zello.key '
                               'ZELLO_ISSUER=WyJNU0dfSVNTVUVSIiwiZGVtbyJd ZELLO_WS_ENDPOINT=wss://zello.io/ws USRP_GAIN_RX_DB=0 USRP_GAIN_TX_DB=-3 LOG_LEVEL=INFO'}


def udp_listening(port: int) -> Optional[bool]:
    """Is something bound to this UDP port (the USRP RX port of the bridge)?"""
    if not port:
        return None
    want = "%04X" % port
    for f in ("/proc/net/udp", "/proc/net/udp6"):
        for line in read(f).splitlines()[1:]:
            parts = line.split()
            if len(parts) > 1 and parts[1].rsplit(":", 1)[-1] == want:
                return True
    return False
