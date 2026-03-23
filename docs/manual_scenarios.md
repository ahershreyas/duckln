# Duckln Manual Scenario Tests

This document records the required manual scenario tests from `docs/tasks.md`.

Use this format for each run:
- `Date:`
- `Tester:`
- `OS:`
- `Shell:`
- `Mode:`
- `Provider:`
- `Model:`
- `Actual Result:`
- `Pass/Fail:`
- `Notes:`

## 1. Missing torch in Wrong Environment

**Requirement Links**
- `R6`
- `R7`
- `R8`

**Plan Links**
- `7`
- `8`
- `9`

**Goal**
- Confirm Duckln distinguishes a missing package from the wrong active Python environment and suggests the smallest next steps.

**Setup**
- Create or use two Python environments.
- Install `torch` in one environment only.
- Activate the environment where `torch` is not installed.

**Steps**
1. Run Duckln in the environment without `torch`.
2. Execute: `python -c "import torch; print(torch.__version__)"`
3. Observe classification, suggested commands, and verification guidance.

**Expected Behavior**
- Duckln classifies the issue as `missing_module` or `pip_python_mismatch`.
- Duckln explains the likely environment mismatch clearly.
- Duckln returns `1–3` exact next commands with purpose labels.
- Duckln prefers safe, high-signal checks such as Python/pip environment inspection before broader fixes.
- Duckln includes a short beginner-friendly explanation of environment mismatch or pip behavior.
- If a suggested fix is run, Duckln proposes targeted verification followed by rerun verification.

**Actual Results**
- `Date:`
- `Tester:`
- `OS:`
- `Shell:`
- `Mode:`
- `Provider:`
- `Model:`
- `Actual Result:`
- `Pass/Fail:`
- `Notes:`

## 2. Broken venv Activation

**Requirement Links**
- `R6`
- `R7`
- `R8`

**Plan Links**
- `7`
- `8`
- `9`

**Goal**
- Confirm Duckln handles broken or missing virtual environment activation cleanly.

**Setup**
- Create a project with a `.venv` reference or expected virtual environment.
- Break activation by removing the activation script or pointing to a missing environment.

**Steps**
1. Run Duckln in the affected project.
2. Execute a command that assumes the venv is active, for example: `python -m pip show torch`
3. Observe diagnosis, suggestions, and verification steps.

**Expected Behavior**
- Duckln identifies the failure as an environment/path issue rather than a generic package failure.
- Duckln suggests exact commands to inspect the environment or reactivate/recreate it.
- Duckln keeps suggestions bounded to `1–3` commands.
- Duckln includes a short explanation of what a virtual environment does.
- Verification guidance checks the environment first, then reruns the original command when safe.

**Actual Results**
- `Date:`
- `Tester:`
- `OS:`
- `Shell:`
- `Mode:`
- `Provider:`
- `Model:`
- `Actual Result:`
- `Pass/Fail:`
- `Notes:`

## 3. File Not Found

**Requirement Links**
- `R6`
- `R7`
- `R8`

**Plan Links**
- `7`
- `8`
- `9`

**Goal**
- Confirm Duckln recognizes missing file/path failures and suggests a narrow verification path.

**Setup**
- Choose a path that does not exist.

**Steps**
1. Run Duckln.
2. Execute: `cat ./does-not-exist.txt`
3. Observe classification, suggestions, and verification sequence.

**Expected Behavior**
- Duckln classifies the failure as `file_not_found`.
- Duckln suggests `1–3` exact commands that check the path or create/fix the missing file when appropriate.
- Duckln avoids unrelated environment advice.
- Verification guidance uses a narrow path check before rerunning the original command.
- On success, Duckln can confirm the original command now works.

**Actual Results**
- `Date:`
- `Tester:`
- `OS:`
- `Shell:`
- `Mode:`
- `Provider:`
- `Model:`
- `Actual Result:`
- `Pass/Fail:`
- `Notes:`

## 4. Permission Denied

**Requirement Links**
- `R6`
- `R7`
- `R8`

**Plan Links**
- `7`
- `8`
- `9`

**Goal**
- Confirm Duckln classifies permission failures safely and does not suggest unsafe escalation by default.

**Setup**
- Choose a file or directory with restricted permissions.

**Steps**
1. Run Duckln.
2. Execute a command that triggers a permission error, for example: `cat /root/secret.txt` or write to a restricted location.
3. Observe diagnosis and suggested next commands.

**Expected Behavior**
- Duckln classifies the issue as `permission_denied`.
- Duckln explains the permission problem in concise human language.
- Duckln suggests the safest/highest-signal next commands first.
- Duckln does not auto-run risky or privileged commands.
- Verification guidance checks the current location or permissions first before broader retries.
- In `HOOTLWO` mode, Duckln only auto-runs whitelisted safe commands and must not auto-run privileged or destructive commands.

**Actual Results**
- `Date:`
- `Tester:`
- `OS:`
- `Shell:`
- `Mode:`
- `Provider:`
- `Model:`
- `Actual Result:`
- `Pass/Fail:`
- `Notes:`

## 5. Torch Compiled Without CUDA

**Requirement Links**
- `R6`
- `R7`
- `R8`

**Plan Links**
- `7`
- `8`
- `9`

**Goal**
- Confirm Duckln identifies CUDA/PyTorch mismatch and gives brief educational guidance plus targeted verification.

**Setup**
- Use a Python environment where `torch` is installed without CUDA support, or simulate the known error output.

**Steps**
1. Run Duckln.
2. Execute a command that triggers the CUDA mismatch, for example:
   - `python -c "import torch; print(torch.cuda.is_available()); x = torch.tensor([1]).cuda()"`
3. Observe classification, next commands, and verification guidance.

**Expected Behavior**
- Duckln classifies the failure as `cuda_torch_mismatch`.
- Duckln explains briefly that CUDA support depends on the installed PyTorch build and machine setup.
- Duckln returns `1–3` exact next commands only.
- Suggested verification includes a targeted CUDA availability check before rerunning the original command.
- Duckln keeps the explanation short and action-first.
- If running on Apple Silicon (M1/M2/M3), Duckln detects that CUDA is not supported and explains this clearly.
- Duckln suggests using CPU or MPS (Metal Performance Shaders) as the correct alternative.
- Duckln avoids suggesting CUDA installation steps on unsupported hardware.

**Actual Results**
- `Date:`
- `Tester:`
- `OS:`
- `Shell:`
- `Mode:`
- `Provider:`
- `Model:`
- `Actual Result:`
- `Pass/Fail:`
- `Notes:`
   
 ## 6. Healthcheck Validation

**Requirement Links**
- `R6`

**Plan Links**
- `12`

**Goal**
- Confirm Duckln validates the current environment and reports clear pass/fail results without exposing sensitive data.

**Setup**
- Use a normal Duckln environment with provider configured.
- Optionally prepare one broken condition (for example, wrong provider key or missing package) to confirm failure reporting.

**Steps**
1. Run Duckln.
2. Execute: `/healthcheck`
3. Observe validation output.

**Expected Behavior**
- Duckln reports Python version, virtual environment status, dependency checks, and provider connectivity.
- Duckln clearly marks checks as pass/fail.
- Duckln suggests concise next steps for failed checks.
- Duckln does not expose secrets, API keys, or sensitive environment data.

**Actual Results**
- `Date:`
- `Tester:`
- `OS:`
- `Shell:`
- `Mode:`
- `Provider:`
- `Model:`
- `Actual Result:`
- `Pass/Fail:`
- `Notes:`  

