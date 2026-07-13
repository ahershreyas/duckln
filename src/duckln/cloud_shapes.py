"""Provider-backed VM shape discovery, categorization, and labelling helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shlex

from duckln.shell import CommandResult, ControlledCommandRunner


_GCP_GPU_NAME_PATTERN = re.compile(r"nvidia[-_]?(?:tesla[-_]?)?([a-z0-9]+)", re.IGNORECASE)
_GPU_KIND_NORMALIZATION = {
    "l4": "L4",
    "t4": "T4",
    "p4": "P4",
    "p100": "P100",
    "v100": "V100",
    "k80": "K80",
    "a10": "A10",
    "a10g": "A10G",
    "a100": "A100",
    "h100": "H100",
    "h200": "H200",
    "b200": "B200",
}


@dataclass(frozen=True)
class DiscoveredCloudShape:
    """One provider-backed VM shape with parsed metadata."""

    provider: str
    name: str
    cpu_count: int
    memory_gb: float
    gpu_kind: str | None
    gpu_count: int
    family: str

    @property
    def has_gpu(self) -> bool:
        return self.gpu_kind is not None and self.gpu_count > 0


@dataclass(frozen=True)
class ShapeCategory:
    """One category bucket presented in the first-tier picker."""

    label: str
    kind: str
    shapes: tuple[DiscoveredCloudShape, ...]


def _normalize_gpu_kind(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().lower()
    match = _GCP_GPU_NAME_PATTERN.search(text)
    token = match.group(1).lower() if match else text.replace("nvidia", "").strip("-_ ").lower()
    if not token:
        return None
    if token in _GPU_KIND_NORMALIZATION:
        return _GPU_KIND_NORMALIZATION[token]
    return token.upper()


def _gcp_family(name: str) -> str:
    return (name or "").split("-", 1)[0].lower()


def _aws_family(name: str) -> str:
    return (name or "").split(".", 1)[0].lower()


def _parse_gcp_shape(entry: dict) -> DiscoveredCloudShape | None:
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    try:
        cpu = int(entry.get("guestCpus") or 0)
        mem_mb = float(entry.get("memoryMb") or 0.0)
    except (TypeError, ValueError):
        return None
    accelerators = entry.get("accelerators") or []
    gpu_kind: str | None = None
    gpu_count = 0
    if isinstance(accelerators, list):
        for accel in accelerators:
            if not isinstance(accel, dict):
                continue
            kind = _normalize_gpu_kind(str(accel.get("guestAcceleratorType") or ""))
            try:
                count = int(accel.get("guestAcceleratorCount") or 0)
            except (TypeError, ValueError):
                count = 0
            if kind and count > 0:
                gpu_kind = kind
                gpu_count += count
    return DiscoveredCloudShape(
        provider="gcp",
        name=name,
        cpu_count=cpu,
        memory_gb=round(mem_mb / 1024.0, 2),
        gpu_kind=gpu_kind,
        gpu_count=gpu_count,
        family=_gcp_family(name),
    )


def _parse_aws_shape(entry: dict) -> DiscoveredCloudShape | None:
    name = str(entry.get("InstanceType") or "").strip()
    if not name:
        return None
    vcpu_info = entry.get("VCpuInfo") or {}
    mem_info = entry.get("MemoryInfo") or {}
    try:
        cpu = int(vcpu_info.get("DefaultVCpus") or 0)
        mem_mib = float(mem_info.get("SizeInMiB") or 0.0)
    except (TypeError, ValueError):
        return None
    gpu_info = entry.get("GpuInfo") or {}
    gpu_kind: str | None = None
    gpu_count = 0
    gpus = gpu_info.get("Gpus") if isinstance(gpu_info, dict) else None
    if isinstance(gpus, list):
        for gpu in gpus:
            if not isinstance(gpu, dict):
                continue
            kind = _normalize_gpu_kind(str(gpu.get("Name") or ""))
            try:
                count = int(gpu.get("Count") or 0)
            except (TypeError, ValueError):
                count = 0
            if kind and count > 0:
                gpu_kind = kind
                gpu_count += count
    return DiscoveredCloudShape(
        provider="aws",
        name=name,
        cpu_count=cpu,
        memory_gb=round(mem_mib / 1024.0, 2),
        gpu_kind=gpu_kind,
        gpu_count=gpu_count,
        family=_aws_family(name),
    )


def discover_all_gcp_shapes(
    *,
    runner: ControlledCommandRunner,
    gcloud_cli: str,
    zone: str,
) -> tuple[tuple[DiscoveredCloudShape, ...], CommandResult | None]:
    """Run `gcloud compute machine-types list --zones <z> --format=json` and parse each row."""

    command = (
        f"{shlex.quote(gcloud_cli)} compute machine-types list "
        f"--zones {shlex.quote(zone)} --format=json"
    )
    result = runner.run(command, timeout_seconds=60)
    if result.exit_code != 0 or result.timed_out:
        return ((), result)
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ((), result)
    shapes: list[DiscoveredCloudShape] = []
    for entry in payload if isinstance(payload, list) else []:
        if isinstance(entry, dict):
            parsed = _parse_gcp_shape(entry)
            if parsed is not None:
                shapes.append(parsed)
    return (tuple(shapes), None)


def discover_all_aws_shapes(
    *,
    runner: ControlledCommandRunner,
    aws_cli: str,
    region: str,
) -> tuple[tuple[DiscoveredCloudShape, ...], CommandResult | None]:
    """Run `aws ec2 describe-instance-types --region <r> --output json --no-cli-pager` and parse each row."""

    command = (
        f"{shlex.quote(aws_cli)} ec2 describe-instance-types "
        f"--region {shlex.quote(region)} --output json --no-cli-pager"
    )
    result = runner.run(command, timeout_seconds=120)
    if result.exit_code != 0 or result.timed_out:
        return ((), result)
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return ((), result)
    shapes: list[DiscoveredCloudShape] = []
    entries = payload.get("InstanceTypes") if isinstance(payload, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            parsed = _parse_aws_shape(entry)
            if parsed is not None:
                shapes.append(parsed)
    return (tuple(shapes), None)


def discover_all_cloud_shapes(
    provider: str,
    *,
    runner: ControlledCommandRunner,
    cli_path: str,
    region_or_zone: str,
) -> tuple[tuple[DiscoveredCloudShape, ...], CommandResult | None]:
    """Provider-agnostic dispatcher returning the full live shape catalogue for the locale."""

    normalized = (provider or "").strip().lower()
    if normalized == "gcp":
        return discover_all_gcp_shapes(runner=runner, gcloud_cli=cli_path, zone=region_or_zone)
    if normalized == "aws":
        return discover_all_aws_shapes(runner=runner, aws_cli=cli_path, region=region_or_zone)
    return ((), None)


_COMPUTE_FAMILY_TOKENS = (
    "c2", "c2d", "c3", "c3d", "c4",
    "c5", "c5n", "c6i", "c6g", "c7g", "c7i", "c8g",
    "hpc", "h3", "z3",
)
_MEMORY_FAMILY_TOKENS = (
    "m1", "m2", "m3",
    "r5", "r6i", "r7i", "r6a", "r5n", "r7a", "r8g",
    "x2", "x1", "u-", "x2idn", "x2iedn", "x2gd",
)
_GENERAL_FAMILY_TOKENS = (
    "e2", "n1", "n2", "n2d", "n4", "t2a", "t2d", "t2",
    "t3", "t3a", "t4g", "m5", "m6i", "m6a", "m7i", "m7a", "m7g", "m8g",
)


def _cpu_category_kind(shape: DiscoveredCloudShape) -> str:
    name = shape.name.lower()
    family = shape.family.lower()
    if "-highmem-" in name or "-megamem-" in name or "-ultramem-" in name:
        return "cpu_memory"
    if "-highcpu-" in name:
        return "cpu_compute"
    if family in _COMPUTE_FAMILY_TOKENS:
        return "cpu_compute"
    if family in _MEMORY_FAMILY_TOKENS:
        return "cpu_memory"
    if family in _GENERAL_FAMILY_TOKENS:
        return "cpu_general"
    return "cpu_other"


_CPU_CATEGORY_LABELS = {
    "cpu_general": "CPU - General Purpose",
    "cpu_compute": "CPU - Compute Optimized",
    "cpu_memory": "CPU - Memory Optimized",
    "cpu_other": "CPU - Other",
}
_CPU_CATEGORY_ORDER = ("cpu_general", "cpu_compute", "cpu_memory", "cpu_other")


def group_shapes_into_categories(
    shapes: tuple[DiscoveredCloudShape, ...],
) -> tuple[ShapeCategory, ...]:
    """Bucket discovered shapes by GPU kind and CPU family signal."""

    gpu_buckets: dict[str, list[DiscoveredCloudShape]] = {}
    cpu_buckets: dict[str, list[DiscoveredCloudShape]] = {}
    for shape in shapes:
        if shape.has_gpu and shape.gpu_kind:
            gpu_buckets.setdefault(shape.gpu_kind, []).append(shape)
            continue
        cpu_buckets.setdefault(_cpu_category_kind(shape), []).append(shape)

    categories: list[ShapeCategory] = []
    for kind in _CPU_CATEGORY_ORDER:
        bucket = cpu_buckets.get(kind)
        if not bucket:
            continue
        bucket.sort(key=lambda s: (s.cpu_count, s.memory_gb, s.name))
        categories.append(
            ShapeCategory(
                label=_CPU_CATEGORY_LABELS[kind],
                kind=kind,
                shapes=tuple(bucket),
            )
        )
    for gpu_kind in sorted(gpu_buckets.keys()):
        bucket = gpu_buckets[gpu_kind]
        bucket.sort(key=lambda s: (s.gpu_count, s.cpu_count, s.memory_gb, s.name))
        categories.append(
            ShapeCategory(
                label=f"GPU - {gpu_kind}",
                kind=f"gpu_{gpu_kind.lower()}",
                shapes=tuple(bucket),
            )
        )
    return tuple(categories)


def derived_default_disk_gb(shape: DiscoveredCloudShape) -> int:
    """Pick a sensible default boot disk size for a freshly created VM of this shape."""

    if shape.has_gpu:
        return 80
    if shape.cpu_count >= 16:
        return 60
    return 30


def _format_memory(value: float) -> str:
    if value >= 100 or value == int(value):
        return f"{int(round(value))}"
    return f"{value:.1f}"


def render_shape_label(shape: DiscoveredCloudShape) -> str:
    """Produce the picker label for one shape (no 'provider-verified' suffix)."""

    disk = derived_default_disk_gb(shape)
    if shape.has_gpu and shape.gpu_kind:
        gpu_label = f"{shape.gpu_count}× {shape.gpu_kind} GPU"
    else:
        family_label = _cpu_category_kind(shape).replace("cpu_", "").replace("_", " ").title()
        gpu_label = f"{family_label} CPU"
    return (
        f"{shape.name} — {shape.cpu_count} CPU — {_format_memory(shape.memory_gb)} GB RAM — "
        f"{disk} GB disk — {gpu_label}"
    )
