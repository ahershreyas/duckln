"""Plan 194 — finish the context_engg.md pipeline: Phase 2 COMPRESS §5 guardrails (F1 pin critical
facts verbatim; F2 tokenizer-accurate budgeting; F3 tool-result masking; F4 model-aware budget) +
Phase 3 long-term recall in the conversation path (F5 repo-profile slice; F6 repo-return recall)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


def _obs(turn, tool, args, payload, ok=True):
    return SimpleNamespace(turn=turn, tool=tool, args=args, payload=payload, ok=ok,
                           error_code="", error_message="")


# --- F1: pin critical facts verbatim -----------------------------------------

class F1PinnedFacts(unittest.TestCase):
    def test_pinned_facts_survive_verbatim_over_a_long_trajectory(self):
        from duckln.harness.loop import render_state_for_llm
        from duckln.harness.state import AgentState

        st = AgentState(initial_input={"step": "build"})
        st.pinned_facts = ["Active incident: VM duckln-vm not accessible", "Active repo: JustHireMe (node, vm)"]
        for i in range(40):
            st.observations.append(_obs(i, "fs.read_file", {"path": f"f{i}.py"}, {"lines": i, "blob": "x" * 400}))
        rendered = render_state_for_llm(st, max_chars=3000)
        # The pinned facts are present verbatim even though the trajectory is folded/truncated.
        self.assertIn("VM duckln-vm not accessible", rendered)
        self.assertIn("Active repo: JustHireMe", rendered)
        self.assertIn("Pinned facts", rendered)


# --- F2: tokenizer-accurate budgeting ----------------------------------------

class F2Budget(unittest.TestCase):
    def test_path_aware_estimate_exceeds_chars_over_4(self):
        from duckln.harness.context_budget import budget_tokens, estimate_tokens

        pathy = "src/duckln/harness/context_budget.py imports a.b.c_d-e"
        self.assertGreater(budget_tokens(pathy), estimate_tokens(pathy))
        self.assertEqual(budget_tokens(""), 1)

    def test_usable_context_is_model_aware(self):
        from duckln.harness.context_budget import usable_context_tokens

        self.assertGreater(usable_context_tokens("claude-opus"), usable_context_tokens("gemma2:9b"))


# --- F3: tool-result masking --------------------------------------------------

class F3Masking(unittest.TestCase):
    def test_older_results_are_masked_newest_is_verbatim(self):
        from duckln.harness.context_budget import assemble_observation_block

        obs = [_obs(i, "fs.read_file", {"path": f"f{i}.json"}, {"note": "BODY" + "y" * 300}) for i in range(4)]
        block = assemble_observation_block(obs, live_window=6)
        # The newest keeps a fuller body; an older live-window result is masked to a short gist (+NB).
        self.assertIn("…(+", block)  # a masked/compact reference appears
        # newest (turn 3) is rendered first and fuller
        first_line = [ln for ln in block.splitlines() if "turn 3" in ln]
        self.assertTrue(first_line and len(first_line[0]) >= 40)


# --- F4: model-aware compaction budget ---------------------------------------

class F4ModelAware(unittest.TestCase):
    def test_small_model_gets_a_tighter_budget(self):
        from duckln.harness.loop import render_state_for_llm
        from duckln.harness.state import AgentState

        st = AgentState(initial_input={"step": "build"})
        for i in range(60):
            st.observations.append(_obs(i, "fs.read_file", {"path": f"f{i}.py"}, {"blob": "z" * 300}))
        small = render_state_for_llm(st, max_chars=100000, model_id="gemma2:9b")   # ~8k window
        big = render_state_for_llm(st, max_chars=100000, model_id="claude-opus")    # ~200k window
        self.assertLess(len(small), len(big))  # small model compacts sooner


# --- F5 / F6: long-term recall ------------------------------------------------

class F5F6LongTerm(unittest.TestCase):
    def test_repo_memory_recall_from_facts_and_prefs(self):
        from duckln.conversation_routes.provider_support import build_repo_memory_recall
        from state.access import write_repo_facts, write_user_preference

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            repo = SimpleNamespace(name="JustHireMe", repo_url="https://github.com/x/JustHireMe")
            self.assertEqual(build_repo_memory_recall(cfg, repo), "")  # nothing remembered → empty
            write_repo_facts(cfg, repo.repo_url, {"archetype": "Tauri desktop app needing a backend venv + PyInstaller"})
            write_user_preference(cfg, key="package_manager", value="pnpm")
            recall = build_repo_memory_recall(cfg, repo)
            self.assertIn("JustHireMe", recall)
            self.assertIn("PyInstaller", recall)
            self.assertIn("pnpm", recall)

    def test_conversation_prompt_injects_repo_slice_only_for_repo_routes(self):
        from duckln.conversation_routes.provider_support import _repo_memory_slice
        from state.access import write_repo_facts

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            repo = SimpleNamespace(name="JustHireMe", repo_url="https://github.com/x/JustHireMe")
            write_repo_facts(cfg, repo.repo_url, {"archetype": "Tauri desktop app needing a backend venv + PyInstaller"})
            slice_text = _repo_memory_slice(cfg, repo)
            self.assertIn("Remembered about this repo", slice_text)
            self.assertIn("PyInstaller", slice_text)

    def test_repo_return_recall_surface_is_silent_without_facts(self):
        from duckln import main

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            repo = SimpleNamespace(name="Fresh", repo_url="https://github.com/x/Fresh")
            seen: list[str] = []
            main._surface_repo_recall(repo, cfg, seen.append)
            self.assertEqual(seen, [])  # no stored facts → silent


if __name__ == "__main__":
    unittest.main()
