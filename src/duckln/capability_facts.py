"""Plan 185: deterministic capability FACTS for the LLM's conversation grounding.

The LLM voices the recommendation + the "why / why not local / why a VM" over these facts; the
facts themselves are gathered deterministically (host probe + the active repo's estimate) and are
never hallucinated and never emitted as canned prose. This is the FLOOR (Plan 160): deterministic
facts + LLM voice. With no usable model the conversation gate (Plan 119) handles it — there is NO
scripted "if weak/down say X" text here; the difference between a weak and a strong model is purely
how well the model reasons over this constant factual floor.
"""

from __future__ import annotations

from pathlib import Path


def active_repo_resource_facts(config_dir):
    """(repo_name, needs_gpu, resource_heavy) for the active repo, from its local clone's README +
    manifests. (None, False, False) when there's no readable repo. Best-effort, never raises."""
    try:
        from state.store import initialize_state_store

        row = initialize_state_store(Path(config_dir)).get_latest_repo_state()
        if row is None:
            return (None, False, False)
        name = str((row.metadata or {}).get("repo_name") or "").strip() or (str(row.repo_key or "").strip() or None)
        loc = str(row.repo_path or (row.metadata or {}).get("install_location") or "").strip()
        readme, reqs = "", ""
        p = Path(loc) if loc else None
        if p is not None and p.is_dir():
            from duckln.repo_bringup import _read_readme_excerpt

            readme = _read_readme_excerpt(p / "README.md")
            for rf in ("requirements.txt", "pyproject.toml", "package.json", "Cargo.toml"):
                if (p / rf).is_file():
                    try:
                        reqs += (p / rf).read_text(encoding="utf-8", errors="ignore")[:2000] + "\n"
                    except Exception:
                        pass
        from duckln.repo_bringup import _repo_is_resource_heavy, _repo_needs_gpu

        needs_gpu = _repo_needs_gpu(requirements_text=reqs, readme=readme)
        blob = (readme + " " + reqs).lower()
        heavy = _repo_is_resource_heavy((), ()) or any(
            t in blob for t in (
                "pytorch", "tensorflow", "cuda", "train", "gpu", "large model", "llm", "diffusion",
                "transformers", "docker build", "cargo build", "webpack", "vite build", "tauri", "electron",
            )
        )
        return (name, bool(needs_gpu), bool(heavy))
    except Exception:
        return (None, False, False)


def gather_capability_facts(config_dir) -> dict:
    """Deterministic host facts + the active-repo signals + a recommended-target SIGNAL + the
    structured REASONS behind it. Pure data — the LLM turns this into prose, not the other way round."""
    facts: dict = {"host": {}, "repo": {}, "recommended_target": None, "reasons": []}
    snap = None
    try:
        from duckln.resource_manager import probe_host_resources

        snap = probe_host_resources()
        facts["host"] = {
            "cpu_cores": snap.cpu_cores, "ram_mb": snap.ram_mb, "gpu_present": snap.gpu_present,
            "gpu_name": snap.gpu_name, "vram_mb": snap.vram_mb,
            "disk_free_mb": snap.disk_free_mb, "disk_total_mb": snap.disk_total_mb,
        }
    except Exception:
        pass
    name, needs_gpu, heavy = active_repo_resource_facts(config_dir)
    facts["repo"] = {"name": name, "needs_gpu": needs_gpu, "resource_heavy": heavy}
    gpu_absent = (snap is None) or (not snap.gpu_present)
    low_ram = bool(snap and snap.ram_mb and snap.ram_mb < 16 * 1024)
    if name:
        if needs_gpu and gpu_absent:
            facts["recommended_target"], facts["reasons"] = "gpu-cloud", ["repo_needs_gpu", "no_discrete_gpu"]
        elif heavy and low_ram:
            facts["recommended_target"], facts["reasons"] = "bigger-vm", ["repo_resource_heavy", "ram_below_16gb"]
        else:
            facts["recommended_target"], facts["reasons"] = "local", ["fits_local"]
    return facts


def render_capability_facts_block(facts: dict) -> str:
    """A compact STRUCTURED facts block for the LLM grounding. The model explains/defends the
    recommendation + 'why' in its OWN words over these; it must use these exact numbers and never
    invent hardware. Empty string when there are no facts to ground."""
    from duckln.resource_manager import _gb

    h = facts.get("host", {}) or {}
    r = facts.get("repo", {}) or {}
    bits: list[str] = []
    if h.get("cpu_cores"):
        bits.append(f"{h['cpu_cores']} CPU cores")
    if h.get("ram_mb"):
        bits.append(f"{_gb(h['ram_mb'])} RAM")
    if h.get("gpu_present"):
        bits.append(f"GPU {h.get('gpu_name') or 'present'}" + (f" ({_gb(h.get('vram_mb', 0))} VRAM)" if h.get("vram_mb") else ""))
    else:
        bits.append("no discrete GPU")
    if h.get("disk_free_mb") and h.get("disk_total_mb"):
        bits.append(f"{_gb(h['disk_free_mb'])} free of {_gb(h['disk_total_mb'])} disk")
    lines: list[str] = []
    if bits:
        lines.append("Machine facts (use these exact numbers; never invent hardware): " + " · ".join(bits) + ".")
    if r.get("name"):
        lines.append(f"Active repo '{r['name']}' signals: needs_gpu={r.get('needs_gpu')}, resource_heavy={r.get('resource_heavy')}.")
        if facts.get("recommended_target"):
            lines.append(
                f"Deterministic recommendation signal: best target = {facts['recommended_target']} "
                f"(reasons: {', '.join(facts.get('reasons') or []) or 'fits_local'}). Explain the recommendation "
                "and the why (incl. why-not-local / why-a-VM) in your OWN words grounded in these facts; offer "
                "honest alternatives. Do not output this signal or these reason codes verbatim."
            )
    return "\n".join(lines)


def capability_facts_block_for(config_dir) -> str:
    """Convenience: gather + render in one call (best-effort, empty string on failure)."""
    try:
        return render_capability_facts_block(gather_capability_facts(config_dir))
    except Exception:
        return ""
