"""Plan 153 — AI/ML: accelerator + environment awareness.
A1 the resource probe senses the target GPU (nvidia-smi → snapshot).
A2 framework_install_command matches the wheel/build to the accelerator.
A3 _repo_needs_gpu detects a GPU-required repo.
B  conda/mamba env setup for environment.yml repos.
"""

from __future__ import annotations

import unittest

import duckln.repo_bringup as rb
from duckln import resource_manager as rmgr
from duckln.diagnostics import framework_install_command, _nearest_cuda_tag


class GpuProbe(unittest.TestCase):
    def test_probe_script_includes_nvidia_smi(self):
        self.assertIn("nvidia-smi", rmgr.RESOURCE_PROBE_SCRIPT)

    def test_parses_gpu_line(self):
        out = ("DUCKLN_CPU:8\nMemTotal:   16000000 kB\nDUCKLN_SWAP:0\n"
               "DUCKLN_GPU:NVIDIA A100-SXM4-40GB, 40960 MiB\nDUCKLN_CUDA:12.1\n")
        snap = rmgr.parse_resource_block(out)
        self.assertTrue(snap.gpu_present)
        self.assertIn("A100", snap.gpu_name)
        self.assertEqual(snap.vram_mb, 40960)
        self.assertEqual(snap.cuda_version, "12.1")
        self.assertEqual(snap.accelerator, "cuda")

    def test_no_gpu_is_cpu(self):
        snap = rmgr.parse_resource_block("DUCKLN_CPU:4\nDUCKLN_GPU:\nDUCKLN_CUDA:\n")
        self.assertFalse(snap.gpu_present)
        self.assertEqual(snap.accelerator, "cpu")


class FrameworkWheelMatcher(unittest.TestCase):
    def test_torch_cuda_uses_matching_index(self):
        cmd = framework_install_command("torch", "cuda", "12.1")
        self.assertIn("download.pytorch.org/whl/cu121", cmd)

    def test_torch_cpu_and_mps(self):
        self.assertIn("/whl/cpu", framework_install_command("torch", "cpu"))
        self.assertNotIn("index-url", framework_install_command("torch", "mps"))  # default = MPS

    def test_tensorflow_per_accelerator(self):
        self.assertIn("tensorflow-macos", framework_install_command("tensorflow", "mps"))
        self.assertIn("tensorflow[and-cuda]", framework_install_command("tensorflow", "cuda"))

    def test_jax(self):
        self.assertIn("jax[cuda12]", framework_install_command("jax", "cuda"))
        self.assertIn("jax-metal", framework_install_command("jax", "mps"))

    def test_version_pin_preserved(self):
        self.assertIn("torch==2.3.0", framework_install_command("torch", "cpu", version="2.3.0"))

    def test_unknown_framework_none(self):
        self.assertIsNone(framework_install_command("numpy", "cuda"))

    def test_nearest_cuda_tag(self):
        self.assertEqual(_nearest_cuda_tag("12.4"), "cu124")
        self.assertEqual(_nearest_cuda_tag("12.2"), "cu121")   # nearest <= 12.2
        self.assertEqual(_nearest_cuda_tag("11.8"), "cu118")
        self.assertEqual(_nearest_cuda_tag("garbage"), "cu121")  # safe default


class GpuNeedDetection(unittest.TestCase):
    def test_gpu_only_dep(self):
        self.assertTrue(rb._repo_needs_gpu(requirements_text="torch\nbitsandbytes==0.43\n"))
        self.assertTrue(rb._repo_needs_gpu(requirements_text="flash-attn\n"))

    def test_readme_statement(self):
        self.assertTrue(rb._repo_needs_gpu(readme="This project requires CUDA 12 and an NVIDIA GPU."))

    def test_diffusion_family(self):
        self.assertTrue(rb._repo_needs_gpu(repo_family=rb.RepoFamily.DIFFUSION_HEAVY))

    def test_plain_repo_does_not_need_gpu(self):
        self.assertFalse(rb._repo_needs_gpu(requirements_text="flask\nrequests\n", readme="a web app"))


class CondaEnvSetup(unittest.TestCase):
    def test_detects_conda_repo(self):
        self.assertTrue(rb._repo_uses_conda(("environment.yml", "README.md")))
        self.assertFalse(rb._repo_uses_conda(("requirements.txt",)))

    def test_env_name_from_yaml(self):
        self.assertEqual(rb._conda_env_name_from_yaml("name: myenv\ndependencies:\n - python=3.11\n"), "myenv")
        self.assertEqual(rb._conda_env_name_from_yaml("dependencies: []\n"), "duckln-env")  # default

    def test_setup_command_is_idempotent_and_installs_miniforge(self):
        cmd = rb._conda_setup_command("myenv")
        self.assertIn("Miniforge3", cmd)               # installs if absent
        self.assertIn("env list", cmd)                  # idempotent skip-if-exists
        self.assertIn("env create -n myenv", cmd)
        self.assertIn("mamba", cmd)                      # mamba preferred

    def test_setup_steps_emitted_only_for_conda_repo(self):
        steps = rb._conda_env_setup_steps(("environment.yml",), environment_yml_text="name: ml\n")
        self.assertEqual(len(steps), 1)
        self.assertIn("conda environment 'ml'", steps[0][0])
        self.assertEqual(rb._conda_env_setup_steps(("requirements.txt",)), ())

    def test_run_prefix_uses_conda_run(self):
        self.assertEqual(rb._conda_run_prefix("ml"), "conda run -n ml")


class MlPlanAssemblyWiring(unittest.TestCase):
    """Plan 153 integration: the helpers wired into plan assembly."""

    def test_detect_dl_frameworks_from_requirements(self):
        self.assertEqual(rb._detect_dl_frameworks("torch==2.3.0\nnumpy\n"), ("torch",))
        self.assertEqual(
            rb._detect_dl_frameworks("tensorflow>=2.16\njax\n"),
            ("tensorflow", "jax"),
        )

    def test_detect_dl_frameworks_pyproject(self):
        self.assertIn("torch", rb._detect_dl_frameworks("", '[project]\ndependencies = ["torch>=2"]\n'))

    def test_detect_dl_frameworks_none_for_plain_repo(self):
        self.assertEqual(rb._detect_dl_frameworks("flask\nrequests\n"), ())

    def test_ml_framework_install_steps_matches_accelerator(self):
        steps = rb._ml_framework_install_steps(("torch",), accelerator="cuda", cuda_version="12.1", venv_pip="pip")
        self.assertEqual(len(steps), 1)
        title, cmd, safety = steps[0]
        self.assertIn("cuda-matched torch", title)
        self.assertIn("download.pytorch.org/whl/cu121", cmd)
        self.assertEqual(safety, "S2")

    def test_ml_framework_install_steps_uses_venv_pip(self):
        steps = rb._ml_framework_install_steps(("torch",), accelerator="cpu", venv_pip="/x/.venv/bin/python -m pip")
        self.assertTrue(steps[0][1].startswith("/x/.venv/bin/python -m pip install"))

    def test_ml_framework_install_steps_skips_unknown(self):
        self.assertEqual(rb._ml_framework_install_steps(("numpy",), accelerator="cuda"), ())


if __name__ == "__main__":
    unittest.main()
