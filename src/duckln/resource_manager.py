"""Plan 111 — Resource sub-agent.

A DETERMINISTIC manager (not an LLM loop — resource/cost decisions are too important
for a weak model) that, for any target (local / local-VM / container / cloud):

- probes available resources (RAM, swap, disk, CPU) on the TARGET and the HOST;
- DERIVES the requirement from real signals (the failure itself, the measured build
  footprint, learned history) — never a hardcoded per-stack minimum;
- detects a crunch proactively (before a heavy build) and reactively (from an error);
- recommends a sensible new size BOUNDED BY HOST CAPACITY (a local VM can't exceed the
  host) and COST-CAREFUL on cloud, always with a numeric, derivation-backed reason;
- scales only on explicit user approval (with an option to enter a custom value);
- logs every event per target so the user can refer back.

All amounts are in MB internally; GB shown to the user. Repo-agnostic — keyed on
resource readings + build class + error signatures.
"""

from __future__ import annotations

import math
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# --- data ---------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceSnapshot:
    ram_mb: int = 0
    swap_on: bool = False
    disk_total_mb: int = 0
    disk_free_mb: int = 0
    disk_used_pct: int = 0
    cpu_cores: int = 0
    # Plan 153 A1: the target's accelerator, sensed at the initial check.
    gpu_present: bool = False
    gpu_name: str = ""
    vram_mb: int = 0
    cuda_version: str = ""

    @property
    def known(self) -> bool:
        return self.ram_mb > 0 or self.disk_total_mb > 0

    @property
    def accelerator(self) -> str:
        """'cuda' when an NVIDIA GPU is present, else 'cpu'. (MPS is a local-host signal set
        separately for an Apple-Silicon target.)"""
        return "cuda" if self.gpu_present else "cpu"


@dataclass(frozen=True)
class ResourceNeed:
    ram_mb: int = 0          # 0 = no RAM requirement derived
    disk_total_mb: int = 0   # desired TOTAL disk (0 = none)
    reason: str = ""


@dataclass(frozen=True)
class ResourceCrunch:
    resource: str            # "ram" | "disk" | "cpu"
    available_mb: int
    used_pct: int = 0
    detail: str = ""


@dataclass(frozen=True)
class ResourceRecommendation:
    resource: str
    current_mb: int
    recommended_mb: int
    min_mb: int
    feasible: bool           # False when even the host can't satisfy it
    explanation: str
    cost_note: str = ""
    host_capped: bool = False


# --- thresholds (clearly-labeled headroom FLOORS, not per-stack minimums) ------

_DISK_DANGER_PCT = 90            # disk at/above this % is a crunch
_MIN_FREE_DISK_FLOOR_MB = 1024   # always want at least ~1 GB free
_HOST_RAM_HEADROOM_MB = 2048     # leave the host ≥2 GB
_HOST_DISK_HEADROOM_MB = 5120    # leave the host ≥5 GB
_RAM_OOM_FACTOR = 2.0            # OOM ⇒ current is too small ⇒ scale up from current

_OOM_TOKENS = ("signal: 9", "sigkill", "out of memory", "cannot allocate memory",
               "oom-kill", "cc1plus: out of memory", "memoryerror", "killed")
_DISK_TOKENS = ("no space left on device", "enospc", "write error: no space",
                "disk quota exceeded", "cannot write: no space", "out of disk")


def crunch_from_error(error_text: str) -> ResourceCrunch | None:
    """Reactive: classify a failure as a resource crunch (or None)."""
    e = (error_text or "").lower()
    timed_out = "timed out" in e
    if any(t in e for t in _DISK_TOKENS):
        return ResourceCrunch(resource="disk", available_mb=0, detail="no space left on device")
    if any(t in e for t in _OOM_TOKENS) and not timed_out:
        return ResourceCrunch(resource="ram", available_mb=0, detail="process killed (out of memory)")
    return None


def detect_crunch(snapshot: ResourceSnapshot, need: ResourceNeed) -> tuple[ResourceCrunch, ...]:
    """Proactive: flag resources short for the derived need or over a danger threshold,
    relative to their OWN current values (no absolute magic minimum)."""
    out: list[ResourceCrunch] = []
    if snapshot.disk_total_mb > 0:
        free_floor = max(_MIN_FREE_DISK_FLOOR_MB, need.disk_total_mb - snapshot.disk_total_mb if need.disk_total_mb else 0)
        if snapshot.disk_used_pct >= _DISK_DANGER_PCT or (free_floor and snapshot.disk_free_mb < free_floor):
            out.append(ResourceCrunch(resource="disk", available_mb=snapshot.disk_free_mb,
                                      used_pct=snapshot.disk_used_pct,
                                      detail=f"{snapshot.disk_free_mb} MB free / {snapshot.disk_used_pct}% used"))
    if need.ram_mb and snapshot.ram_mb and snapshot.ram_mb < need.ram_mb and not snapshot.swap_on:
        out.append(ResourceCrunch(resource="ram", available_mb=snapshot.ram_mb,
                                  detail=f"{snapshot.ram_mb} MB RAM, no swap"))
    return tuple(out)


def estimate_requirement(
    *,
    snapshot: ResourceSnapshot,
    error_text: str = "",
    repo_size_mb: int = 0,
    heavy_build: bool = False,
    learned_ram_mb: int = 0,
    learned_disk_mb: int = 0,
) -> ResourceNeed:
    """DERIVE the requirement from real signals — NOT a hardcoded per-stack minimum.
    Priority: (1) the failure itself (scale relative to current), (2) measured footprint
    (repo/build size), (3) learned history. Floors are only a last-resort headroom."""
    e = (error_text or "").lower()
    ram_mb = 0
    disk_total_mb = 0
    reasons: list[str] = []

    # (1) From the failure — the current amount is provably insufficient.
    if any(t in e for t in _OOM_TOKENS) and "timed out" not in e:
        base = learned_ram_mb or snapshot.ram_mb
        if base:
            ram_mb = int(math.ceil(base * _RAM_OOM_FACTOR / 1024.0)) * 1024  # round up to GB
            reasons.append(f"OOM at {base} MB RAM → need ≳{_RAM_OOM_FACTOR:g}× ({ram_mb} MB)")
    if any(t in e for t in _DISK_TOKENS):
        extra = learned_disk_mb or max(repo_size_mb * 3, 5120)  # build output ≈ a few× source
        disk_total_mb = snapshot.disk_total_mb + extra
        reasons.append(f"disk full → grow by ~{extra} MB (build footprint ≈3× repo)")

    # (2)/(3) Proactive (no error): a heavy build wants headroom on free disk.
    if heavy_build and not e and snapshot.disk_total_mb:
        want_free = learned_disk_mb or max(repo_size_mb * 3, 5120)
        if snapshot.disk_free_mb < want_free:
            disk_total_mb = snapshot.disk_total_mb + (want_free - snapshot.disk_free_mb)
            reasons.append(f"heavy build needs ~{want_free} MB free; only {snapshot.disk_free_mb} MB now")

    return ResourceNeed(ram_mb=ram_mb, disk_total_mb=disk_total_mb, reason="; ".join(reasons))


def _gb(mb: int) -> str:
    return f"{mb / 1024:.1f} GB"


def recommend(
    crunch: ResourceCrunch,
    *,
    snapshot: ResourceSnapshot,
    need: ResourceNeed,
    host: ResourceSnapshot | None,
    execution_target: str,
    vm_name: str | None = None,
) -> ResourceRecommendation:
    """A sensible new size derived from current + the deficit, BOUNDED by host capacity
    (local) and cost-careful (cloud). Numbers + derivation, never a constant."""
    from duckln.execution_targets import is_local_hosted_target
    is_local_hosted = is_local_hosted_target(execution_target)  # Plan 155 F1: normalized (docker→container)
    if crunch.resource == "ram":
        current = snapshot.ram_mb
        want = need.ram_mb or int(math.ceil(current * _RAM_OOM_FACTOR / 1024.0)) * 1024
        host_cap = (host.ram_mb - _HOST_RAM_HEADROOM_MB) if (host and is_local_hosted and host.ram_mb) else want
        recommended = min(want, host_cap) if is_local_hosted else want
        feasible = recommended > current
        host_capped = is_local_hosted and host is not None and want > host_cap
        expl = (
            f"RAM is {_gb(current)} and the build was killed (out of memory). "
            f"Recommend {_gb(recommended)} (derived: ≳{_RAM_OOM_FACTOR:g}× current). {need.reason}".strip()
        )
        if host_capped:
            expl += f" — capped by your host ({_gb(host.ram_mb)} total; leaving {_gb(_HOST_RAM_HEADROOM_MB)} free)."
        if not feasible:
            expl += " Your host can't give the VM more RAM — free host memory or run this on cloud."
        cost = "Cloud RAM costs money — this is the smallest size that fits." if execution_target in ("aws", "gcp") else ""
        return ResourceRecommendation("ram", current, recommended, want, feasible, expl, cost, host_capped)

    # disk
    current = snapshot.disk_total_mb
    want = need.disk_total_mb or (current + max(_MIN_FREE_DISK_FLOOR_MB, 5120))
    host_cap = (host.disk_free_mb + current - _HOST_DISK_HEADROOM_MB) if (host and is_local_hosted and host.disk_total_mb) else want
    recommended = min(want, host_cap) if is_local_hosted else want
    feasible = recommended > current
    host_capped = is_local_hosted and host is not None and want > host_cap
    expl = (
        f"Disk is {crunch.used_pct or snapshot.disk_used_pct}% full — only {_gb(snapshot.disk_free_mb)} free of {_gb(current)}. "
        f"Recommend growing disk to {_gb(recommended)}. {need.reason}".strip()
    )
    if host_capped:
        expl += f" — capped by your host's free space (leaving {_gb(_HOST_DISK_HEADROOM_MB)})."
    if not feasible:
        expl += " Your host doesn't have enough free disk — free host space or use cloud."
    cost = "Cloud storage costs money — this is the smallest bump that fits." if execution_target in ("aws", "gcp") else ""
    return ResourceRecommendation("disk", current, recommended, want, feasible, expl, cost, host_capped)


def multipass_resize_commands(rec: ResourceRecommendation, vm_name: str) -> tuple[str, ...]:
    """Resize a multipass VM (disk only grows; requires stop/start).
    Plan 143 F1: `multipass set …disk` grows the VIRTUAL disk but NOT the guest filesystem
    — without growpart+resize2fs the partition stays the old size and `df` is still full
    ("still tight after resize"). After start, expand the root partition + ext4 INSIDE the
    VM so the new space is actually usable (auto-detect /dev/sda|vda|nvme; idempotent)."""
    gb = max(1, int(math.ceil(rec.recommended_mb / 1024.0)))
    key = "memory" if rec.resource == "ram" else "disk"
    cmds = [
        f"multipass stop {vm_name}",
        f"multipass set local.{vm_name}.{key}={gb}G",
        f"multipass start {vm_name}",
    ]
    if rec.resource == "disk":
        # bash parameter expansion → robust across /dev/sda1, /dev/vda1, /dev/nvme0n1p1.
        grow = (
            'R=$(findmnt -no SOURCE /); P=${R##*[!0-9]}; D=${R%$P}; D=${D%p}; '
            'sudo growpart "$D" "$P" 2>/dev/null || true; sudo resize2fs "$R" 2>/dev/null || true'
        )
        cmds.append(f"multipass exec {vm_name} -- sudo bash -lc {shlex.quote(grow)}")
    return tuple(cmds)


def reclaim_commands(*, execution_target: str = "local", aggressive: bool = False) -> tuple[str, ...]:
    """Plan 127: SAFE disk reclamation for a tight VM/host — clear only package-manager
    CACHES and stale temp build dirs, NEVER the repo or user data. Each command is
    `|| true` so a missing tool doesn't fail the step. Repo-agnostic; reclaims the space
    that accumulates across repeated heavy runs (npm/pip/cargo/HF caches, apt lists).
    Plan 143 F2: `aggressive=True` (a genuine disk-FULL crunch) ALSO removes the real disk
    HOGS — the Rust `target/` dirs, the whole cargo registry, `~/.cache`, and `node_modules`
    — all rebuilt/re-fetched on demand, never the repo source/user data. Use it only when
    the disk is actually full (it forces a rebuild), not on a routine retry."""
    cmds = [
        "npm cache clean --force 2>/dev/null || true",
        "yarn cache clean 2>/dev/null || true",
        "pnpm store prune 2>/dev/null || true",
        "pip cache purge 2>/dev/null || true",
        "rm -rf ~/.cache/pip 2>/dev/null || true",
        # Cargo registry caches (downloaded crates/sources) — safe to drop; re-fetched on demand.
        "rm -rf ~/.cargo/registry/cache ~/.cargo/registry/src 2>/dev/null || true",
        # Old temp build scratch (never the project dir). Plan 137: also stale partial
        # pip/npm/cargo temp extractions left by an interrupted install.
        "rm -rf /tmp/duckln-* /tmp/pip-* /tmp/npm-* /tmp/cargo-install* /tmp/*.partial 2>/dev/null || true",
        # Plan 133 F3: stale per-repo BUILD SCRATCH that accumulates across re-runs and
        # fills the disk — PyInstaller workpath/distpath (`build_cache`, `.codex-temp-
        # sidecar`) at the repo root or a backend subdir. Regenerated on rebuild, safe to
        # drop. (cwd is the repo root when this runs; `*/` covers backend/ etc.)
        "rm -rf .codex-temp-sidecar */.codex-temp-sidecar build_cache */build_cache 2>/dev/null || true",
    ]
    if aggressive:
        # Plan 143 F2: the REAL disk hogs on a full disk (GBs). All rebuilt/re-fetched —
        # never the repo source or user data. cwd is the repo root; `*/` covers subdirs.
        cmds += [
            # Rust build output (the single biggest consumer for a Tauri/Rust build).
            "rm -rf target */target src-tauri/target 2>/dev/null || true",
            # The full cargo registry + git checkouts (re-downloaded on the next build).
            "rm -rf ~/.cargo/registry ~/.cargo/git 2>/dev/null || true",
            # User caches (HF, pip wheels, generic xdg cache).
            "rm -rf ~/.cache/* 2>/dev/null || true",
            # node_modules (reinstalled by the guarded install step on the next run).
            "rm -rf node_modules */node_modules 2>/dev/null || true",
            # PyInstaller dist + stale build dirs.
            "rm -rf dist */dist build */build 2>/dev/null || true",
        ]
    if execution_target in {"vm", "aws", "gcp", "ssh"}:
        cmds.append("sudo apt-get clean 2>/dev/null || true")
        # Plan 137: an interrupted apt strands partially-downloaded `.deb`s in the
        # archives/partial dir — clear them explicitly so a half-finished apt is reclaimed.
        cmds.append("sudo rm -rf /var/cache/apt/archives/partial/*.deb 2>/dev/null || true")
    return tuple(cmds)


# --- probing ------------------------------------------------------------------

# Labeled, single-quote-free probe (survives `bash -lc '<script>'` wrapping). df -Pm
# reports MB; the rest is parsed in Python (no awk/cut single-quotes).
RESOURCE_PROBE_SCRIPT = (
    "df -Pm / 2>/dev/null; "
    "echo DUCKLN_CPU:$(nproc 2>/dev/null); "
    "grep MemTotal /proc/meminfo 2>/dev/null; "
    "swapon --show 2>/dev/null | grep -q . && echo DUCKLN_SWAP:1 || echo DUCKLN_SWAP:0; "
    # Plan 153 A1: sense the GPU on the TARGET (NO single quotes — the whole script is wrapped
    # in `bash -lc '<script>'`, so single quotes would break it; double quotes are safe).
    "echo DUCKLN_GPU:$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1); "
    "echo DUCKLN_CUDA:$(nvidia-smi 2>/dev/null | grep -oE \"CUDA Version: [0-9.]+\" | grep -oE \"[0-9.]+\" | head -1)"
)


def parse_resource_block(stdout: str) -> ResourceSnapshot:
    """Parse the RESOURCE_PROBE_SCRIPT output into a snapshot. Tolerant of noise."""
    ram_mb = disk_total = disk_free = used_pct = cpu = vram_mb = 0
    swap_on = gpu_present = False
    gpu_name = cuda_version = ""
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if line.startswith("DUCKLN_CPU:"):
            d = line.split(":", 1)[1].strip()
            cpu = int(d) if d.isdigit() else cpu
        elif line.startswith("DUCKLN_SWAP:"):
            swap_on = line.strip().endswith("1")
        elif line.startswith("DUCKLN_GPU:"):
            # "DUCKLN_GPU:NVIDIA A100-SXM4-40GB, 40960 MiB" (empty when no GPU)
            payload = line.split(":", 1)[1].strip()
            if payload:
                gpu_present = True
                fields = [p.strip() for p in payload.split(",")]
                gpu_name = fields[0]
                if len(fields) > 1:
                    vm = re.search(r"(\d+)", fields[1])
                    if vm:
                        vram_mb = int(vm.group(1))
        elif line.startswith("DUCKLN_CUDA:"):
            cuda_version = line.split(":", 1)[1].strip()
        elif line.startswith("MemTotal:"):
            m = re.search(r"(\d+)", line)
            if m:
                ram_mb = int(m.group(1)) // 1024  # kB → MB
        elif "/" in line and "%" in line and "Filesystem" not in line:
            # df -Pm row: fs 1M-blocks used avail cap% mount
            parts = line.split()
            if len(parts) >= 6 and parts[1].isdigit() and parts[3].isdigit():
                disk_total = int(parts[1])
                disk_free = int(parts[3])
                pm = re.search(r"(\d+)%", line)
                used_pct = int(pm.group(1)) if pm else 0
    return ResourceSnapshot(ram_mb=ram_mb, swap_on=swap_on, disk_total_mb=disk_total,
                            disk_free_mb=disk_free, disk_used_pct=used_pct, cpu_cores=cpu,
                            gpu_present=gpu_present, gpu_name=gpu_name, vram_mb=vram_mb,
                            cuda_version=cuda_version)


def probe_host_resources() -> ResourceSnapshot:
    """The HOST machine's resources (caps how big a local VM/container can get)."""
    import os
    import shutil

    disk_total = disk_free = used_pct = ram_mb = 0
    try:
        du = shutil.disk_usage("/")
        disk_total = du.total // (1024 * 1024)
        disk_free = du.free // (1024 * 1024)
        used_pct = int(100 * (du.total - du.free) / du.total) if du.total else 0
    except OSError:
        pass
    try:
        ram_mb = (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        ram_mb = 0
    return ResourceSnapshot(ram_mb=ram_mb, disk_total_mb=disk_total, disk_free_mb=disk_free,
                            disk_used_pct=used_pct, cpu_cores=os.cpu_count() or 0)


def probe_target_resources(*, config_dir, execution_target: str, vm_name: str | None) -> ResourceSnapshot:
    """Probe the active TARGET (local/vm/container/cloud) via the canonical wrapper."""
    if (execution_target or "local").lower() == "local":
        return probe_host_resources()
    try:
        from duckln.repo_bringup import _wrap_command_for_execution_target
        from duckln.shell import ControlledCommandRunner

        wrapped, _meta = _wrap_command_for_execution_target(
            config_dir=Path(config_dir), execution_target=execution_target,
            command=RESOURCE_PROBE_SCRIPT, cwd=None, preferred_vm_name=vm_name,
        )
        if wrapped is None:
            return ResourceSnapshot()
        res = ControlledCommandRunner(execution_target="local").run(wrapped, timeout_seconds=30.0)
        return parse_resource_block(getattr(res, "stdout", "") or "")
    except Exception:
        return ResourceSnapshot()


def apply_resize(
    rec: ResourceRecommendation,
    *,
    execution_target: str,
    vm_name: str | None,
    approve: Callable[[str], bool] | None,
    prompt_value: Callable[[str, str], str | None] | None,
    display: Callable[[str], None],
) -> bool:
    """Ask the user (approve recommended / enter custom / decline) and, on approval,
    resize. NEVER scales without explicit approval; never exceeds host capacity."""
    display(rec.explanation + (f" {rec.cost_note}" if rec.cost_note else ""))
    if not rec.feasible:
        return False
    if approve is None:
        return False
    chosen_mb = rec.recommended_mb
    # Offer a custom value first (option to enter your own), else approve the default.
    if prompt_value is not None:
        entered = prompt_value(
            f"Set {rec.resource} to how many GB? (Enter = recommended {rec.recommended_mb // 1024} GB, "
            f"min {max(1, rec.min_mb // 1024)} GB)",
            str(rec.recommended_mb // 1024),
        )
        if entered is not None and str(entered).strip():
            m = re.search(r"(\d+)", str(entered))
            if m:
                chosen_mb = int(m.group(1)) * 1024
    restart_note = " (brief restart)" if execution_target == "vm" else ""
    if not approve(f"Resize {rec.resource} of '{vm_name}' to {chosen_mb // 1024} GB now?{restart_note} [y/n]"):
        display("Left resources unchanged.")
        return False

    applied = ResourceRecommendation(rec.resource, rec.current_mb, chosen_mb, rec.min_mb, True, rec.explanation)
    commands = resize_commands_for_target(applied, execution_target=execution_target, vm_name=vm_name)
    if not commands:
        display(
            "Automatic resize isn't available for this target/resource yet — "
            + ("Docker can't grow a container's disk in place; recreate it with a larger volume."
               if (execution_target == "container" and rec.resource == "disk")
               else f"apply the {rec.resource} change manually, then retry.")
        )
        return False
    from duckln.shell import ControlledCommandRunner

    runner = ControlledCommandRunner(execution_target="local")
    for cmd in commands:
        display(f"   · {cmd}")
        try:
            runner.run(cmd, timeout_seconds=180.0)
        except Exception as exc:
            display(f"Resize step failed: {exc}")
            return False
    display(f"✓ {rec.resource} of '{vm_name}' set to {chosen_mb // 1024} GB.")
    return True


def resize_commands_for_target(rec: ResourceRecommendation, *, execution_target: str, vm_name: str | None) -> tuple[str, ...]:
    """Plan 112/114: the resize commands for the actual target. VM → multipass; Container
    → `docker update` (RAM/CPU, live, no recreate; disk in place isn't supported → ()).
    Cloud → handled by the cloud branch (see resize_commands_for_cloud). () = unsupported."""
    from duckln.execution_targets import normalize_execution_target
    et = normalize_execution_target(execution_target)  # Plan 155 F1: docker→container
    gb = max(1, int(math.ceil(rec.recommended_mb / 1024.0)))
    if et == "vm" and vm_name:
        return multipass_resize_commands(rec, vm_name)
    if et == "container" and vm_name:
        if rec.resource == "ram":
            return (f"docker update --memory {gb}g --memory-swap {gb}g {vm_name}",)
        if rec.resource == "cpu":
            return (f"docker update --cpus {max(1, rec.recommended_mb)} {vm_name}",)
        return ()  # container rootfs disk can't grow in place
    return ()
