"""Deterministic diagnostics and error classification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import importlib.util
import platform
import re
import sys
from typing import TYPE_CHECKING, Any

from duckln.modes import ControlMode

if TYPE_CHECKING:
    from duckln.config import AppConfig


REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b(ghp|gho|ghu|github_pat)_[A-Za-z0-9_]{8,}\b"), "[REDACTED_TOKEN]"),
    # Plan 78 Fix G: Authorization headers and OAuth/authorization-code URL params.
    (re.compile(r"(?i)(authorization)\s*:\s*(?:bearer|basic|token)\s+\S+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)([?&](?:code|state|access_token|id_token|refresh_token|client_secret)=)[^\s&#\"']+"), r"\1[REDACTED]"),
    # Unambiguous OAuth secret names anywhere (key=value / key: value), even outside a URL.
    (re.compile(r"(?i)\b(access_token|id_token|refresh_token|client_secret)\s*[:=]\s*[^\s,;&#\"']+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"), r"\1=[REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "[REDACTED_MAC]"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b"), "[REDACTED_IP]"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "[REDACTED_IP]"),
    (re.compile(r"\b(?:\+?\d[\d .()-]{7,}\d)\b"), "[REDACTED_PHONE]"),
)

class ErrorCategory(str, Enum):
    """Deterministic error classes for Python and AI environment issues."""

    MISSING_MODULE = "missing_module"
    NODE_DEP_MISSING = "node_dep_missing"                 # Plan 173: a node package isn't installed (vite/tsc/devDeps)
    PIP_PYTHON_MISMATCH = "pip_python_mismatch"
    FILE_NOT_FOUND = "file_not_found"
    PERMISSION_DENIED = "permission_denied"
    CUDA_TORCH_MISMATCH = "cuda_torch_mismatch"
    # Plan 80 Fix 6: language-agnostic categories for the error→fix rule library.
    ENGINE_MISMATCH = "engine_mismatch"
    COMMAND_NOT_FOUND = "command_not_found"
    PORT_IN_USE = "port_in_use"
    MISSING_COMPILER = "missing_compiler"
    # Plan 81 Fix 5/7: more ecosystems + a non-fixable BLOCK category.
    CODEGEN_REQUIRED = "codegen_required"
    NATIVE_LIB_MISSING = "native_lib_missing"
    BROWSER_DEPS_MISSING = "browser_deps_missing"
    SERVICE_UNREACHABLE = "service_unreachable"
    PRIVATE_REPO = "private_repo"
    ALREADY_PRESENT = "already_present"
    OUT_OF_MEMORY = "out_of_memory"
    DISK_FULL = "disk_full"
    RESOURCE_LIMIT = "resource_limit"
    # Plan 119: a polyglot prebuild needs another ecosystem's env that wasn't set
    # up first (e.g. a JS `build:sidecar` needing backend/.venv/bin/python).
    MISSING_VENV = "missing_venv"
    # Plan 124 (macOS / AI-ML):
    PACKAGE_MANAGER_MISSING = "package_manager_missing"   # e.g. Homebrew not installed
    TORCH_WHEEL_MISMATCH = "torch_wheel_mismatch"         # CUDA-pinned torch on a non-CUDA host
    TF_MACOS_REQUIRED = "tf_macos_required"               # plain tensorflow on Apple Silicon
    GPU_UNAVAILABLE = "gpu_unavailable"                   # repo hard-requires CUDA; host has none
    MODEL_AUTH_REQUIRED = "model_auth_required"           # gated HuggingFace model needs a token
    APT_CONFLICT = "apt_conflict"                         # Plan 138: held/broken apt packages (dependency conflict)
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeterministicFix:
    """Plan 80 Fix 6: a concrete fix derived from a known error pattern, BEFORE any
    LLM. `safety_class` is advisory (reclassified by the planner)."""

    category: ErrorCategory
    cause: str
    fix_title: str
    fix_command: str
    safety_class: str = "S3"
    verification: str | None = None
    # Plan 81 Fix 7: when True there is NO auto-fix — Duckln must BLOCK and ask the
    # user (e.g. a private repo needs a token). `fix_command` is empty in that case.
    block: bool = False
    block_question: str = ""


def _apt_or_brew(pkg: str, *, execution_target: str) -> str:
    is_linux = execution_target in {"vm", "aws", "gcp", "ssh"}
    if is_linux:
        return f"sudo apt-get update && sudo apt-get install -y {pkg}"
    return f"brew install {pkg}"


# Plan 153 A2: torch's published CUDA wheel builds (newest first). Map a detected CUDA
# version to the nearest supported build that is <= the driver's CUDA.
_TORCH_CUDA_BUILDS: tuple[tuple[tuple[int, int], str], ...] = (
    ((12, 4), "cu124"), ((12, 1), "cu121"), ((11, 8), "cu118"),
)


def _nearest_cuda_tag(cuda_version: str) -> str:
    """Nearest torch CUDA wheel tag (cu124/cu121/cu118) <= the detected CUDA; safe default cu121."""
    try:
        parts = re.findall(r"\d+", str(cuda_version or ""))
        det = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except Exception:
        return "cu121"
    for ver, tag in _TORCH_CUDA_BUILDS:
        if ver <= det:
            return tag
    return "cu118"


def framework_install_command(
    framework: str, accelerator: str, cuda_version: str = "", *, pip: str = "pip", version: str = "",
) -> str | None:
    """Plan 153 A2: the install command for a DL framework MATCHED to the target accelerator
    (`cuda` | `mps` | `cpu`). Proactive (chosen at install from the detected accelerator) AND
    reused by the reactive macOS rules. `version` preserves a repo's pin on the primary
    package; the accelerator only steers the build/index. Returns None for an unknown framework.
    Signal-keyed (framework name + accelerator), never repo-name-keyed."""
    fw = (framework or "").strip().lower()
    acc = (accelerator or "cpu").strip().lower()
    if fw in ("torch", "pytorch", "torchvision", "torchaudio"):
        primary = "torch" if fw in ("torch", "pytorch") else fw
        pin = f"{primary}=={version}" if version else primary
        pkgs = f"{pin} torchvision torchaudio" if primary == "torch" else pin
        if acc == "cuda":
            return f"{pip} install {pkgs} --index-url https://download.pytorch.org/whl/{_nearest_cuda_tag(cuda_version)}"
        if acc == "mps":
            return f"{pip} install {pkgs}"  # the default wheel uses MPS on Apple Silicon
        return f"{pip} install {pkgs} --index-url https://download.pytorch.org/whl/cpu"
    if fw == "jax":
        if acc == "cuda":
            # Plan 175 A3: match the JAX CUDA wheel to the sensed CUDA major (cuda11 vs cuda12).
            _jax_cuda = "cuda11_pip" if str(cuda_version or "").strip().startswith("11") else "cuda12"
            return f'{pip} install "jax[{_jax_cuda}]"'
        if acc == "mps":
            return f"{pip} install jax-metal"
        return f"{pip} install jax"
    if fw in ("tensorflow", "tf"):
        if acc == "mps":
            return f"{pip} install tensorflow-macos tensorflow-metal"
        if acc == "cuda":
            return f'{pip} install "tensorflow[and-cuda]"'
        return f"{pip} install tensorflow"
    # Plan 154: Apple MLX — Metal-only, Apple-Silicon-only (no accelerator variants). The
    # caller installs it solely on a local Apple-Silicon target; on Linux it's skipped + warned.
    if fw in ("mlx", "mlx-lm", "mlx-vlm", "mlx-data"):
        pin = f"{fw}=={version}" if version else fw
        return f"{pip} install {pin}"
    # Plan 175 A1: ONNX Runtime — the GPU build is a SEPARATE wheel (`onnxruntime-gpu`, CUDA);
    # CPU and Apple Silicon use the default `onnxruntime` (the macOS wheel bundles the CoreML EP).
    if fw in ("onnxruntime", "onnx", "onnxruntime-gpu"):
        if acc == "cuda":
            return f"{pip} install onnxruntime-gpu"
        return f"{pip} install onnxruntime"
    return None


def match_deterministic_fix(
    *,
    stderr: str,
    stdout: str = "",
    exit_code: int | None = None,
    command: str = "",
    execution_target: str = "local",
) -> DeterministicFix | None:
    """Map a known failure pattern to a concrete fix WITHOUT calling the LLM.
    Returns None when no rule matches (caller falls back to LLM/web)."""
    blob = f"{stderr or ''}\n{stdout or ''}"
    low = blob.lower()
    is_linux = execution_target in {"vm", "aws", "gcp", "ssh"}
    # Plan 124: a LOCAL target on a Darwin host is macOS (Apple Silicon = no CUDA).
    is_macos = execution_target == "local" and platform.system() == "Darwin"

    # Plan 136 F2: SELF-CORRECT a malformed command Duckln itself ran. A model-typo'd
    # stray-slash builtin (`/cd …`) fails with exit 127 / "No such file or directory" —
    # the most trivially fixable error there is. Strip the slash and re-run; NEVER report
    # "no automatic fix available" for this. (Self-inflicted → safe to auto-apply.)
    command_not_found = (
        exit_code == 127
        or "no such file or directory" in low
        or "command not found" in low
    )
    if command_not_found and command:
        from duckln.shell import sanitize_shell_command as _sanitize_cmd

        repaired = _sanitize_cmd(command)
        if repaired and repaired != command:
            bad_token = command.strip().split(maxsplit=1)[0] if command.strip() else command
            return DeterministicFix(
                category=ErrorCategory.COMMAND_NOT_FOUND,
                cause=(
                    f"The command was malformed — `{bad_token}` is not a command (a stray "
                    "leading slash on a shell builtin). The correct form drops the slash."
                ),
                fix_title="Re-run the corrected command",
                fix_command=repaired, safety_class="S2", verification=None,
            )
        # Plan 196 F5: a WELL-FORMED `cd <dir> && …` that fails with "no such file or
        # directory" because <dir> genuinely doesn't exist (NOT a `/cd` typo — sanitize
        # left it unchanged). The Plan-136 rule above can't help; without this the
        # reasoning/web recovery dead-ends ("searched but couldn't confirm a fix").
        # Deterministic remedy: create the dir first, then re-run. Repo-scoped, S2.
        if "no such file or directory" in low:
            _cd_m = re.match(
                r"""\s*cd\s+(?:"([^"]+)"|'([^']+)'|(\S+))""", command
            )
            if _cd_m:
                import shlex as _shlex

                _target = _cd_m.group(1) or _cd_m.group(2) or _cd_m.group(3)
                return DeterministicFix(
                    category=ErrorCategory.COMMAND_NOT_FOUND,
                    cause=(
                        f"The working directory `{_target}` doesn't exist yet, so the "
                        "step couldn't change into it. Creating it first fixes the step."
                    ),
                    fix_title="Create the missing directory, then re-run",
                    fix_command=f"mkdir -p {_shlex.quote(_target)} && {command}",
                    safety_class="S2", verification=None,
                )

    # 0. Out of memory / process killed during a heavy build (Plan 89). A small VM
    # OOMs compiling Tauri/GTK Rust crates and the kernel SIGKILLs rustc/cc1plus —
    # this is a RESOURCE problem with a deterministic remedy (swap + single-threaded
    # build), not something a web search or a tiny model can fix. Require an explicit
    # kill/OOM token (never a bare "memory" mention) to avoid false positives.
    oom_token = any(
        t in low for t in (
            "signal: 9", "sigkill", "oom-kill", "out of memory", "cannot allocate memory",
            "virtual memory exhausted", "cc1plus: out of memory", "cc: out of memory",
            "memoryerror", "fatal error: killed",
        )
    )
    build_kill = bool(re.search(r"\bkilled\b", low)) and any(
        t in low for t in ("compil", "rustc", "cargo", "build", "webpack", "linker", "collect2")
    )
    if oom_token or build_kill:
        # Add swap when none is active, then pin the build to one job so the compile
        # fits in RAM. Writing ~/.cargo/config.toml makes the retried `tauri dev`
        # build single-threaded without changing the run command.
        if is_linux:
            cmd = (
                "if ! sudo swapon --show 2>/dev/null | grep -q .; then "
                "sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && "
                "sudo mkswap /swapfile && sudo swapon /swapfile; fi; "
                "mkdir -p ~/.cargo && printf '[build]\\njobs = 1\\n' > ~/.cargo/config.toml"
            )
            verify = "sudo swapon --show | grep -q ."
        else:
            # macOS manages swap dynamically; cap parallelism instead.
            cmd = "mkdir -p ~/.cargo && printf '[build]\\njobs = 1\\n' > ~/.cargo/config.toml"
            verify = None
        return DeterministicFix(
            category=ErrorCategory.OUT_OF_MEMORY,
            cause="The build ran out of memory and was killed (SIGKILL) — the VM has too little RAM to compile this in parallel.",
            fix_title="Add swap and build single-threaded",
            fix_command=cmd, safety_class="S3", verification=verify,
        )

    # 0b. Polyglot prebuild needs a Python venv that was never created (Plan 119).
    # e.g. a JS `npm run build:sidecar` runs `node scripts/build-sidecar.mjs` which
    # expects `<repo>/backend/.venv/bin/python`. Deterministic remedy: create the
    # venv AT THE PATH THE ERROR NAMES and install that dir's declared deps —
    # repo-agnostic (driven by the error's path, not any repo name).
    venv_match = re.search(
        r"(?:virtual environment|venv|virtualenv)[^\n:]*not found:\s*([^\s'\"]+/bin/python[0-9.]*)",
        blob, re.IGNORECASE,
    )
    if not venv_match:
        venv_match = re.search(r"([^\s'\"]+/(?:\.venv|venv|env)/bin/python[0-9.]*):?\s*no such file", low)
    if venv_match:
        py_path = venv_match.group(1)
        venv_dir = py_path.rsplit("/bin/", 1)[0]              # .../backend/.venv
        venv_name = venv_dir.rsplit("/", 1)[-1] or ".venv"     # .venv
        backend_dir = venv_dir.rsplit("/", 1)[0] or "."        # .../backend
        cmd = (
            f'cd "{backend_dir}" && python3 -m venv "{venv_name}" && '
            f'"{venv_name}/bin/pip" install --upgrade pip && '
            # Plan 148 F5: best-effort deps install — a flat-layout editable build failing must
            # NOT skip the PyInstaller install below (the cause of "No module named PyInstaller").
            f'{{ if [ -f requirements.txt ]; then "{venv_name}/bin/pip" install -r requirements.txt || true; fi; '
            f'if [ -f pyproject.toml ]; then "{venv_name}/bin/pip" install -e . 2>/dev/null || '
            f'"{venv_name}/bin/pip" install . 2>/dev/null || true; fi; }} && '
            # Plan 144 F2: a polyglot prebuild (e.g. `build:sidecar`) commonly runs
            # `python -m PyInstaller` from THIS venv, but the tool is rarely declared in
            # requirements — install it up front when a PyInstaller `*.spec` is present, so
            # the build resolves in ONE recovery pass instead of failing at the next stage.
            f'{{ if ls *.spec >/dev/null 2>&1 && grep -qiE "pyinstaller|Analysis\\(" *.spec 2>/dev/null; then '
            f'"{venv_name}/bin/python" -c "import PyInstaller" 2>/dev/null || "{venv_name}/bin/pip" install pyinstaller || true; fi; }} && '
            # Plan 144 F2: stamp the completion marker so the partial-cleanup treats this as
            # a COMPLETE venv and never deletes it (the marker distinguishes complete from
            # half-built — without it, the next fix re-run wiped this freshly-created venv).
            f'touch "{venv_name}/.duckln-deps-ok"'
        )
        return DeterministicFix(
            category=ErrorCategory.MISSING_VENV,
            cause=(
                f"A prebuild step expects a Python virtual environment at `{venv_dir}`, "
                "but it was never created — this repo's Python backend must be set up before its build runs."
            ),
            fix_title=f"Create the Python venv at {venv_dir} and install its dependencies",
            fix_command=cmd, safety_class="S2",
            verification=f'test -x "{venv_dir}/bin/python" || test -x "{venv_dir}/bin/python3"',
        )

    # 0c. Homebrew missing on macOS — the official installer needs the user's sudo
    # password, so Duckln can't `brew install` it unattended. BLOCK with the exact
    # one-liner instead of looping (must precede the command-not-found rule).
    if is_macos and ("command not found: brew" in low or re.search(r"\bbrew:\s*command not found", low)):
        return DeterministicFix(
            category=ErrorCategory.PACKAGE_MANAGER_MISSING,
            cause="Homebrew is not installed, so Duckln can't install macOS toolchains/libraries.",
            fix_title="Install Homebrew", fix_command="", safety_class="S3",
            block=True,
            block_question=(
                "Homebrew is required to install tools on macOS. Install it with:\n"
                '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"\n'
                "then re-run. (It needs your password, so Duckln can't do it unattended.)"
            ),
        )

    # 0d. A CUDA-pinned PyTorch wheel can't install on macOS (no CUDA) → reinstall the
    # default CPU/MPS wheel into the venv. Must precede the generic missing-module rule.
    if is_macos and "torch" in low and (
        "no matching distribution found for torch" in low
        or ("could not find a version that satisfies the requirement torch" in low)
        or re.search(r"torch==[0-9.]+\+cu", low)
        or ("unsupported platform" in low and "torch" in low)
    ):
        venv_m = re.search(r"([^\s:'\"]+/(?:\.venv|venv|env))/bin/python", blob)
        pip = f'"{venv_m.group(1)}/bin/pip"' if venv_m else "python3 -m pip"
        return DeterministicFix(
            category=ErrorCategory.TORCH_WHEEL_MISMATCH,
            cause="The repo pins a CUDA build of PyTorch, which has no macOS wheel — macOS uses the CPU/MPS build.",
            fix_title="Install the macOS (CPU/MPS) PyTorch wheel",
            fix_command=f"{pip} install --upgrade --force-reinstall torch",
            safety_class="S2", verification=f"{pip} show torch",
        )

    # 0d-tf. Plain `tensorflow` has no Apple-Silicon wheel → install `tensorflow-macos`
    # (+ `tensorflow-metal` for GPU) into the venv. Framework parity with the torch rule.
    if is_macos and "tensorflow" in low and (
        "no matching distribution found for tensorflow" in low
        or ("could not find a version that satisfies the requirement tensorflow" in low)
        or ("unsupported platform" in low and "tensorflow" in low)
    ):
        venv_m = re.search(r"([^\s:'\"]+/(?:\.venv|venv|env))/bin/python", blob)
        pip = f'"{venv_m.group(1)}/bin/pip"' if venv_m else "python3 -m pip"
        return DeterministicFix(
            category=ErrorCategory.TF_MACOS_REQUIRED,
            cause="Plain TensorFlow has no Apple-Silicon wheel — macOS uses tensorflow-macos (+ tensorflow-metal for GPU).",
            fix_title="Install tensorflow-macos (+ tensorflow-metal)",
            fix_command=f"{pip} install tensorflow-macos tensorflow-metal",
            safety_class="S2", verification=f"{pip} show tensorflow-macos",
        )

    # 0e. Repo HARD-requires a CUDA GPU but the host has none (macOS) → honest BLOCK.
    if is_macos and (
        "torch not compiled with cuda" in low
        or "no cuda-capable device" in low
        or "found no nvidia driver" in low
        or "cuda error: no kernel image" in low
        or ("assert" in low and "torch.cuda.is_available" in low)
    ):
        return DeterministicFix(
            category=ErrorCategory.GPU_UNAVAILABLE,
            cause="This repo requires an NVIDIA CUDA GPU, which a Mac doesn't have (Macs use MPS/CPU).",
            fix_title="Run on a CUDA GPU", fix_command="", safety_class="S3",
            block=True,
            block_question=(
                "This repo needs an NVIDIA CUDA GPU. On a Mac, use a CPU/MPS mode if the repo "
                "supports it, otherwise run it on a Linux GPU VM/cloud (try `/cloud`)."
            ),
        )

    # 0f. A GATED HuggingFace model needs a token — ask, don't hang on the interactive prompt.
    if ("huggingface" in low or "huggingface.co" in low or "hf.co" in low) and (
        "gated repo" in low or "access to model" in low or "you must be authenticated" in low
        or "is restricted" in low or ("401" in low and "model" in low) or "gated and you" in low
    ):
        return DeterministicFix(
            category=ErrorCategory.MODEL_AUTH_REQUIRED,
            cause="This model is gated on HuggingFace and needs an access token.",
            fix_title="Provide a HuggingFace token", fix_command="", safety_class="S3",
            block=True,
            block_question=(
                "This HuggingFace model is gated. Accept its license on huggingface.co, create a token "
                "(Settings → Access Tokens), then set it (`export HF_TOKEN=hf_…`) and re-run."
            ),
        )

    # 1. Node engine mismatch (EBADENGINE / "Vite requires Node 20.19+").
    if "ebadengine" in low or re.search(r"requires node(?:\.js)?", low):
        # Anchor on the REQUIRED version ("requires Node 20.19+"), not the current one.
        m = re.search(r"requires node(?:\.js)?\s*(?:version\s*)?(?:>=?\s*|v)?(\d{2})", low)
        major = m.group(1) if m else "20"
        if is_linux:
            cmd = f"curl -fsSL https://deb.nodesource.com/setup_{major}.x | sudo -E bash - && sudo apt-get install -y nodejs"
        else:
            cmd = f"brew install node@{major} && brew link --overwrite --force node@{major}"
        return DeterministicFix(
            category=ErrorCategory.ENGINE_MISMATCH,
            cause=f"The toolchain requires Node.js {major}+, but an older Node is installed.",
            fix_title=f"Install Node.js {major}", fix_command=cmd, safety_class="S3",
            verification="node --version",
        )

    # 2. Command not found (missing tool).
    m = re.search(r"(?:command not found:\s*|: command not found|: not found)\s*", low)
    cnf = re.search(r"([a-z0-9_.+-]+):\s*command not found|command not found:\s*([a-z0-9_.+-]+)", low)
    if cnf:
        tool = (cnf.group(1) or cnf.group(2) or "").strip()
        if tool:
            if tool in {"pnpm", "yarn", "bun"}:
                cmd = f"npm install -g {tool}"
            elif tool in {"node", "npm"}:
                cmd = _apt_or_brew("nodejs npm" if is_linux else "node", execution_target=execution_target)
            else:
                cmd = _apt_or_brew(tool, execution_target=execution_target)
            return DeterministicFix(
                category=ErrorCategory.COMMAND_NOT_FOUND,
                cause=f"`{tool}` is not installed or not on PATH.",
                fix_title=f"Install {tool}", fix_command=cmd, safety_class="S3",
                verification=f"command -v {tool}",
            )

    # Plan 138 F2: apt DEPENDENCY CONFLICT / held broken packages. READ the real error —
    # don't mis-fire the compiler rule just because the apt output LISTS `node-gyp` as an
    # unmet dependency. The fix RESOLVES the conflict, it never installs a compiler. Placed
    # ABOVE the node-gyp/compiler rule so a broken-packages situation wins.
    if any(k in low for k in ("held broken packages", "unable to correct problems", "unmet dependencies")) or "conflicts:" in low:
        if "nodejs" in low and "npm" in low:
            # nodesource `nodejs` bundles npm and CONFLICTS with the distro `npm` package —
            # install nodejs and drop the conflicting npm (`npm-` removes it during install).
            cmd = "sudo apt-get install -y nodejs npm- || sudo apt-get --fix-broken install -y"
        else:
            cmd = "sudo apt-get --fix-broken install -y"
        return DeterministicFix(
            category=ErrorCategory.APT_CONFLICT,
            cause="apt has a dependency conflict / held broken packages — resolve the conflict (not install a compiler).",
            fix_title="Resolve the apt package conflict", fix_command=cmd, safety_class="S3",
            verification=None,
        )

    # 3. Native build toolchain missing. Plan 138 F2: require a GENUINE gyp/compile error
    # (`gyp ERR!`, `node-gyp rebuild`) — NOT a bare `node-gyp` token, which also appears in
    # an apt `Depends: node-gyp` listing (handled by the apt-conflict rule above).
    if any(k in low for k in ("gyp err", "node-gyp rebuild", "gcc: not found", "make: not found", "g++: not found", "cc1plus")):
        cmd = "sudo apt-get update && sudo apt-get install -y build-essential python3" if is_linux else "xcode-select --install"
        return DeterministicFix(
            category=ErrorCategory.MISSING_COMPILER,
            cause="A native build toolchain (compiler/make) is missing.",
            fix_title="Install build toolchain", fix_command=cmd, safety_class="S3",
            verification="gcc --version",
        )

    # 4. Python missing module. Plan 122: match BOTH the quoted import form
    # (`No module named 'flask'`) AND the unquoted `python -m` form
    # (`No module named PyInstaller`) — the latter never matched before. When the
    # error names a venv python (`…/.venv/bin/python: No module named X`), install X
    # INTO THAT VENV (where it's needed), not system Python. Verify with `pip show`
    # (an install/import-name mismatch like PyInstaller would falsely fail `import`).
    mm = re.search(r"no module named ['\"]?([a-z0-9_.\-]+)['\"]?", low)
    if mm:
        module = mm.group(1).split(".")[0]
        venv_m = re.search(r"([^\s:'\"]+/(?:\.venv|venv|env))/bin/python", blob)
        if venv_m:
            pip = f'"{venv_m.group(1)}/bin/pip"'
            fix_command = f"{pip} install {module}"
            verification = f"{pip} show {module}"
        else:
            fix_command = f"python3 -m pip install {module}"
            verification = f"python3 -m pip show {module}"
        return DeterministicFix(
            category=ErrorCategory.MISSING_MODULE,
            cause=f"Python module `{module}` is not installed in the environment that runs it.",
            fix_title=f"Install {module}", fix_command=fix_command,
            safety_class="S2", verification=verification,
        )

    # 4b. Plan 173 F1: a NODE dependency is missing — the build/run can't resolve a package.
    # Install the project's node deps WITH devDependencies in the failing dir (the build tools
    # vite / @vitejs/plugin-react / typescript are devDependencies a default install provides).
    # Covers vite/rollup `[UNRESOLVED_IMPORT]`, ESM `Cannot find package`, `ERR_MODULE_NOT_FOUND`,
    # CJS `Cannot find module`, and npm's "This is not the tsc command…" (TypeScript not installed).
    # Python `No module named` is matched ABOVE, so it never reaches here. Model-free + repo-scoped.
    _node_dep_missing = (
        "err_module_not_found" in low
        or "cannot find package" in low
        or "[unresolved_import]" in low
        or "this is not the tsc command" in low
        or ("cannot find module" in low and any(h in low for h in ("node", ".ts", ".js", ".mjs", "vite", "node_modules")))
        or ("could not resolve" in low and any(h in low for h in ("vite", "rollup", "esbuild", "node_modules", ".ts", ".tsx", ".jsx", ".mjs", "import")))
    )
    if _node_dep_missing:
        cmdl = (command or "").lower()
        if "pnpm" in cmdl:
            install = "pnpm install --prod=false"
        elif "yarn" in cmdl:
            install = "yarn install --production=false"
        elif "bun" in cmdl:
            install = "bun install"
        else:
            install = "npm install --include=dev"
        pkg_m = re.search(r"(?:cannot find package|could not resolve|cannot find module)\s+['\"]([^'\"]+)['\"]", low)
        pkg = pkg_m.group(1) if pkg_m else ""
        top = pkg.split("/")[0] if pkg and not pkg.startswith(".") else ""
        verification = f"test -d node_modules/{top}" if top else "test -d node_modules"
        return DeterministicFix(
            category=ErrorCategory.NODE_DEP_MISSING,
            cause=(f"Node dependency `{pkg}` is missing — the build/run can't resolve it "
                   "(build tools like vite/@vitejs/plugin-react/typescript are devDependencies)." if pkg
                   else "A node dependency is missing — install the project deps (incl. devDependencies)."),
            fix_title="Install node dependencies (incl. devDependencies)",
            fix_command=install, safety_class="S2", verification=verification,
        )

    # 5. Port already in use.
    if "eaddrinuse" in low or "address already in use" in low or "port already in use" in low:
        pm = re.search(r":(\d{3,5})\b", blob) or re.search(r"port\s+(\d{3,5})", low)
        if pm:
            port = pm.group(1)
            return DeterministicFix(
                category=ErrorCategory.PORT_IN_USE,
                cause=f"Port {port} is already in use by another process.",
                fix_title=f"Free port {port}",
                fix_command=f"lsof -ti tcp:{port} | xargs -r kill",
                safety_class="S3", verification=f"! lsof -ti tcp:{port}",
            )

    # 6. Permission denied (avoid blind sudo — fix ownership of the working dir).
    # Plan 85 Fix 2: a git SSH "publickey" denial is an AUTH problem, not a file
    # ownership one — let the private-repo rule below handle it.
    if ("eacces" in low or "permission denied" in low) and "publickey" not in low:
        return DeterministicFix(
            category=ErrorCategory.PERMISSION_DENIED,
            cause="A file/directory permission prevented the step.",
            fix_title="Fix ownership of the project directory",
            fix_command="sudo chown -R $(id -u):$(id -g) .",
            safety_class="S3", verification=None,
        )

    # Plan 85 Fix 2: a clone that fails because the repo is ALREADY present is NOT a
    # failure — continue (the verify `test -d .git` passes). Never a "private" block.
    if "already exists and is not an empty directory" in low or (
        "already exists" in low and "git clone" in (command or "").lower()
    ):
        return DeterministicFix(
            category=ErrorCategory.ALREADY_PRESENT,
            cause="The repository is already cloned at the destination.",
            fix_title="Use the existing checkout",
            fix_command="echo duckln-already-cloned", safety_class="S0",
            verification=None,
        )

    # Plan 81 Fix 7 / Plan 85 Fix 2: a GENUINELY private/unreachable repo — NOT
    # auto-fixable. Flag ONLY on real auth/not-found/DNS signals in stderr — NOT on a
    # bare exit code 128 (git uses 128 for many benign cases like "already exists").
    if any(k in low for k in ("could not read username", "repository not found",
                              "authentication failed", "403 forbidden", "fatal: could not read",
                              "permission denied (publickey)", "could not resolve host",
                              "remote: not found", "terminal prompts disabled",
                              "invalid username or password", "access denied")):
        return DeterministicFix(
            category=ErrorCategory.PRIVATE_REPO,
            cause="The repository appears to be private or unreachable.",
            fix_title="Provide repository access",
            fix_command="", safety_class="S0", block=True,
            block_question="This repo looks private or unreachable — provide an access token (or correct the URL) so Duckln can clone it.",
        )

    # Plan 81 Fix 5: Prisma client not generated.
    if "@prisma/client did not initialize" in low or "prisma generate" in low or "did not initialize yet" in low:
        return DeterministicFix(
            category=ErrorCategory.CODEGEN_REQUIRED,
            cause="The Prisma client has not been generated.",
            fix_title="Generate the Prisma client", fix_command="npx prisma generate",
            safety_class="S1", verification=None,
        )

    # Plan 81 Fix 5: missing native libraries / headers (Python wheels, C builds).
    native_lib = None
    if "pg_config executable not found" in low or "libpq-fe.h" in low:
        native_lib = ("libpq-dev", "Postgres client headers")
    elif "python.h" in low and "no such file" in low:
        native_lib = ("python3-dev", "Python dev headers")
    elif "fatal error: openssl" in low or "openssl/opensslv.h" in low:
        native_lib = ("libssl-dev", "OpenSSL dev headers")
    elif "ffi.h" in low:
        native_lib = ("libffi-dev", "libffi dev headers")
    if native_lib:
        pkg, label = native_lib
        cmd = _apt_or_brew(pkg, execution_target=execution_target)
        return DeterministicFix(
            category=ErrorCategory.NATIVE_LIB_MISSING,
            cause=f"A native build needs {label} ({pkg}).",
            fix_title=f"Install {pkg}", fix_command=cmd, safety_class="S3", verification=None,
        )

    # Plan 81 Fix 5: Playwright / Puppeteer browser deps.
    if "host system is missing dependencies" in low or "browsertype.launch" in low or "failed to launch the browser" in low:
        return DeterministicFix(
            category=ErrorCategory.BROWSER_DEPS_MISSING,
            cause="Browser automation needs its system dependencies + browsers installed.",
            fix_title="Install Playwright browsers + deps",
            fix_command="npx playwright install --with-deps", safety_class="S3", verification=None,
        )

    # Plan 81 Fix 5: a backing service isn't reachable (links to compose provisioning).
    if "econnrefused" in low or "could not connect to server" in low or "connection refused" in low:
        if re.search(r"5432|postgres", low):
            return DeterministicFix(
                category=ErrorCategory.SERVICE_UNREACHABLE,
                cause="The app can't reach Postgres — the database service isn't running.",
                fix_title="Start Postgres via docker-compose",
                fix_command="docker compose up -d", safety_class="S2", verification="docker compose ps",
            )
        if re.search(r"6379|redis", low):
            return DeterministicFix(
                category=ErrorCategory.SERVICE_UNREACHABLE,
                cause="The app can't reach Redis — the cache service isn't running.",
                fix_title="Start Redis via docker-compose",
                fix_command="docker compose up -d", safety_class="S2", verification="docker compose ps",
            )

    return None


@dataclass(frozen=True)
class ClassificationResult:
    """Classification result for stderr text."""

    category: ErrorCategory
    confidence: float
    reason: str


@dataclass(frozen=True)
class MinimalContext:
    """Minimal diagnostic context suitable for privacy-first payloads."""

    command: str
    category: ErrorCategory
    redacted_stderr: str
    diagnostics: dict[str, str]


@dataclass(frozen=True)
class PayloadBundle:
    """LLM-safe payload built according to the active control mode."""

    mode: ControlMode
    payload: dict[str, Any]


@dataclass(frozen=True)
class HealthcheckReport:
    """Result of validating the current environment and provider connectivity."""

    ok: bool
    lines: tuple[str, ...]

    def render(self) -> str:
        return "\n".join(self.lines)


def redact_sensitive_data(text: str) -> str:
    """Mask obvious secrets and PII before diagnostics leave Duckln."""

    redacted = text
    for pattern, replacement in REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def classify_error(stderr: str) -> ClassificationResult:
    """Classify common Python and AI environment failures deterministically."""

    normalized = stderr.lower()

    if "modulenotfounderror" in normalized:
        return ClassificationResult(
            category=ErrorCategory.MISSING_MODULE,
            confidence=0.98,
            reason="Detected ModuleNotFoundError in stderr.",
        )

    if ("no module named" in normalized and "pip" in normalized) or (
        "which pip" in normalized and "which python" in normalized
    ):
        return ClassificationResult(
            category=ErrorCategory.PIP_PYTHON_MISMATCH,
            confidence=0.85,
            reason="Detected pip/python environment mismatch indicators.",
        )

    if any(token in normalized for token in ("no such file or directory", "filenotfounderror", "not found")):
        return ClassificationResult(
            category=ErrorCategory.FILE_NOT_FOUND,
            confidence=0.9,
            reason="Detected missing file or path indicators.",
        )

    if "permission denied" in normalized:
        return ClassificationResult(
            category=ErrorCategory.PERMISSION_DENIED,
            confidence=0.95,
            reason="Detected permission denied indicators.",
        )

    if any(token in normalized for token in ("cuda", "torch not compiled with cuda", "libcudart", "cudnn")):
        return ClassificationResult(
            category=ErrorCategory.CUDA_TORCH_MISMATCH,
            confidence=0.88,
            reason="Detected CUDA or torch environment mismatch indicators.",
        )

    return ClassificationResult(
        category=ErrorCategory.UNKNOWN,
        confidence=0.2,
        reason="No high-confidence deterministic classifier matched.",
    )


def gather_minimal_context(
    *,
    command: str,
    stderr: str,
    python_version: str | None = None,
    pip_version: str | None = None,
    environment_notes: dict[str, str] | None = None,
) -> MinimalContext:
    """Collect only the minimum diagnostic context needed for the current failure."""

    classification = classify_error(stderr)
    diagnostics: dict[str, str] = {}

    if python_version and classification.category in {
        ErrorCategory.MISSING_MODULE,
        ErrorCategory.PIP_PYTHON_MISMATCH,
        ErrorCategory.CUDA_TORCH_MISMATCH,
    }:
        diagnostics["python_version"] = python_version

    if pip_version and classification.category in {
        ErrorCategory.MISSING_MODULE,
        ErrorCategory.PIP_PYTHON_MISMATCH,
    }:
        diagnostics["pip_version"] = pip_version

    if environment_notes:
        for key, value in environment_notes.items():
            if value:
                diagnostics[key] = value

    return MinimalContext(
        command=command,
        category=classification.category,
        redacted_stderr=redact_sensitive_data(stderr),
        diagnostics=diagnostics,
    )


def build_minimal_payload(mode: ControlMode, context: MinimalContext) -> PayloadBundle:
    """Build a privacy-first LLM payload according to the active control mode."""

    payload: dict[str, Any] = {
        "command": context.command,
        "error_category": context.category.value,
        "stderr": context.redacted_stderr,
    }

    if mode is ControlMode.HITL:
        return PayloadBundle(mode=mode, payload=payload)

    if mode is ControlMode.HOTL:
        payload["diagnostics"] = dict(context.diagnostics)
        return PayloadBundle(mode=mode, payload=payload)

    payload["diagnostics"] = dict(context.diagnostics)
    payload["context_scope"] = "controlled"
    return PayloadBundle(mode=mode, payload=payload)


def run_healthcheck(config: AppConfig, *, client: Any | None = None) -> HealthcheckReport:
    """Validate environment basics and provider connectivity with clear pass/fail lines."""

    from duckln.ai_client import get_provider_adapter

    lines: list[str] = []
    checks_ok = True

    python_ok = sys.version_info >= (3, 11)
    lines.append(_format_check("Python", python_ok, f"{sys.version.split()[0]} detected"))
    checks_ok = checks_ok and python_ok

    pip_ok = importlib.util.find_spec("pip") is not None
    lines.append(_format_check("pip", pip_ok, "pip module available" if pip_ok else "pip module not available"))
    checks_ok = checks_ok and pip_ok

    httpx_ok = importlib.util.find_spec("httpx") is not None
    lines.append(_format_check("httpx", httpx_ok, "httpx installed" if httpx_ok else "httpx missing"))
    checks_ok = checks_ok and httpx_ok

    inquirer_ok = importlib.util.find_spec("InquirerPy") is not None
    lines.append(
        _format_check(
            "InquirerPy",
            inquirer_ok,
            "InquirerPy installed" if inquirer_ok else "InquirerPy missing",
        )
    )
    checks_ok = checks_ok and inquirer_ok

    adapter = get_provider_adapter(config.provider)
    validation = adapter.validate_api_key(config.api_key, client=client)
    provider_ok = validation.ok
    lines.append(
        _format_check(
            f"{config.provider.label} connectivity",
            provider_ok,
            validation.message,
        )
    )
    checks_ok = checks_ok and provider_ok

    if validation.ok:
        model_validation = adapter.validate_model(
            config.api_key,
            config.model,
            client=client,
            models=validation.models,
        )
        model_ok = model_validation.ok
        lines.append(_format_check("Configured model", model_ok, model_validation.message))
        checks_ok = checks_ok and model_ok
    else:
        lines.append(_format_check("Configured model", False, "Skipped because provider validation failed."))
        checks_ok = False

    # Plan 132 D1: warn (don't block) when the model is too small to drive Duckln's
    # reasoning loop — so a tiny local model doesn't silently degrade to fast-path-only.
    try:
        from duckln.recovery import model_is_reasoning_capable

        if not model_is_reasoning_capable(config.model or ""):
            lines.append(_format_check(
                "Reasoning capability", True,
                f"'{config.model}' is small — Duckln's reasoning (plan/recover) needs a capable "
                "model (GPT/Claude/large) to self-correct novel issues; it will fall back to "
                "deterministic fixes + honest stops otherwise.",
            ))
    except Exception:
        pass

    return HealthcheckReport(ok=checks_ok, lines=tuple(lines))


def _format_check(name: str, ok: bool, detail: str) -> str:
    status = "PASS" if ok else "FAIL"
    return f"[{status}] {name}: {redact_sensitive_data(detail)}"
