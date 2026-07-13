"""Plan 175 — production audit, first landed slice:
- G1/G2: the aware-not-dumb interaction primitive (propose → explain → Yes/No/write-your-own) +
  the failure-proposal builder (comprehend the error; deterministic + optional-LLM recommendation).
- A1: ONNX Runtime gets an accelerator-matched wheel.
- A2: an honest message about AMD/ROCm + Intel GPUs (no silent CPU wheel).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from duckln.interaction import (
    ACCEPT,
    CUSTOM,
    REJECT,
    build_failure_proposal,
    propose_and_confirm,
)
from duckln.diagnostics import framework_install_command
from duckln.repo_bringup import _detect_dl_frameworks


class G1ProposeAndConfirm(unittest.TestCase):
    def test_select_accept_reject_custom(self):
        out: list[str] = []
        # Accept
        d = propose_and_confirm(situation="X failed.", recommendation="run `npm install`", why="deps missing",
                                display=out.append, select=lambda m, opts: opts[0])
        self.assertEqual(d.outcome, ACCEPT)
        # Reject
        d = propose_and_confirm(situation="X failed.", recommendation="run `npm install`",
                                display=out.append, select=lambda m, opts: opts[1])
        self.assertEqual(d.outcome, REJECT)
        # Custom → text_prompt supplies the instruction
        d = propose_and_confirm(situation="X failed.", recommendation="run `npm install`",
                                display=out.append, select=lambda m, opts: opts[2],
                                text_prompt=lambda m, dflt: "use pnpm instead")
        self.assertEqual(d.outcome, CUSTOM)
        self.assertEqual(d.custom_text, "use pnpm instead")
        # The situation + recommendation were shown.
        self.assertTrue(any("X failed." in s for s in out))

    def test_binary_fallback(self):
        d = propose_and_confirm(situation="s", recommendation="r", display=lambda _t: None,
                                approve=lambda _m: True)
        self.assertEqual(d.outcome, ACCEPT)
        # No → custom via follow-up text
        d = propose_and_confirm(situation="s", recommendation="r", display=lambda _t: None,
                                approve=lambda _m: False, text_prompt=lambda m, dflt: "do Y")
        self.assertEqual(d.outcome, CUSTOM)
        self.assertEqual(d.custom_text, "do Y")
        # No channel at all → reject (safe)
        d = propose_and_confirm(situation="s", recommendation="r", display=lambda _t: None)
        self.assertEqual(d.outcome, REJECT)


class G2FailureProposal(unittest.TestCase):
    def test_with_deterministic_fix(self):
        from duckln.diagnostics import match_deterministic_fix
        fx = match_deterministic_fix(stderr="Cannot find package 'vite'", command="npm run build", execution_target="vm")
        sit, rec, why = build_failure_proposal(failed_command="npm run build", error_text="Cannot find package 'vite'",
                                               repo_name="JustHireMe", det_fix=fx)
        self.assertIn("JustHireMe", sit)
        self.assertIn("npm install", rec)
        self.assertTrue(why)

    def test_llm_proposal_when_no_det_fix(self):
        sit, rec, why = build_failure_proposal(
            failed_command="weird", error_text="some novel error", det_fix=None,
            llm_client=lambda **k: '{"recommendation": "bump the toolchain", "why": "it is stale"}',
        )
        self.assertEqual(rec, "bump the toolchain")
        self.assertEqual(why, "it is stale")

    def test_honest_when_nothing(self):
        sit, rec, why = build_failure_proposal(failed_command="x", error_text="y", det_fix=None, llm_client=None)
        self.assertIn("don't have a confident", rec.lower())
        self.assertIn("x", sit)


class A1OnnxRuntime(unittest.TestCase):
    def test_onnx_wheel_matches_accelerator(self):
        self.assertEqual(framework_install_command("onnxruntime", "cuda"), "pip install onnxruntime-gpu")
        self.assertEqual(framework_install_command("onnxruntime", "cpu"), "pip install onnxruntime")
        self.assertEqual(framework_install_command("onnxruntime", "mps"), "pip install onnxruntime")

    def test_onnx_detected(self):
        self.assertIn("onnxruntime", _detect_dl_frameworks("onnxruntime-gpu==1.18\n"))
        self.assertIn("onnxruntime", _detect_dl_frameworks("onnxruntime\n"))
        self.assertNotIn("onnxruntime", _detect_dl_frameworks("flask\n"))


class A2RocmHonestMessage(unittest.TestCase):
    def test_honest_amd_rocm_message_present(self):
        # The GPU-needed-but-absent branch must name AMD ROCm / Intel honestly (not silent CPU).
        src = Path("src/duckln/repo_bringup.py").read_text(encoding="utf-8")
        self.assertIn("AMD ROCm and Intel GPUs aren't", src)


class A3JaxCudaMatch(unittest.TestCase):
    def test_jax_cuda_version_matched(self):
        self.assertEqual(framework_install_command("jax", "cuda", "11.8"), 'pip install "jax[cuda11_pip]"')
        self.assertEqual(framework_install_command("jax", "cuda", "12.1"), 'pip install "jax[cuda12]"')
        self.assertEqual(framework_install_command("jax", "cuda", ""), 'pip install "jax[cuda12]"')


class G3F1AwareRecovery(unittest.TestCase):
    """G3 amendment-pause is an aware proposal; F1 honest-stop surfaces what was investigated."""

    def test_wording_present(self):
        src = Path("src/duckln/repo_bringup.py").read_text(encoding="utf-8")
        # G3 (Plan 176 F3 wired the helper): the amendment-pause uses build_failure_proposal and
        # offers apply/skip/redirect.
        self.assertIn("Best approach: ", src)
        self.assertIn("tell me what you'd prefer instead", src)
        # F1: the honest-stop surfaces the investigated evidence.
        self.assertIn("I checked:", src)


class C1PlanLifecycleDocumented(unittest.TestCase):
    def test_dormant_note(self):
        txt = Path("src/duckln/plan_lifecycle.py").read_text(encoding="utf-8")
        self.assertIn("DORMANT-BY-DESIGN", txt)


if __name__ == "__main__":
    unittest.main()
