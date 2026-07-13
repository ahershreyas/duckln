"""Plan 185 — LLM-voice the capability/recommendation advisor over DETERMINISTIC facts.
The deterministic layer supplies FACTS + a target signal + reasons (the floor); the LLM writes all
the prose (recommendation + 'why'); no hardcoded response sentences; weak/strong by architecture."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _snap(*, ram_mb=8192, gpu=False, cpu=8):
    from duckln.resource_manager import ResourceSnapshot

    return ResourceSnapshot(
        ram_mb=ram_mb, cpu_cores=cpu, gpu_present=gpu, gpu_name=("NVIDIA" if gpu else ""),
        vram_mb=(8192 if gpu else 0), disk_free_mb=120_000, disk_total_mb=240_000,
    )


class F1FactsBlock(unittest.TestCase):
    def test_gpu_repo_no_host_gpu_recommends_cloud(self):
        from duckln import capability_facts as cf

        with patch.object(cf, "active_repo_resource_facts", return_value=("MyML", True, True)), \
             patch("duckln.resource_manager.probe_host_resources", return_value=_snap(gpu=False, ram_mb=8192)):
            facts = cf.gather_capability_facts("/tmp")
        self.assertEqual(facts["recommended_target"], "gpu-cloud")
        self.assertIn("no_discrete_gpu", facts["reasons"])
        block = cf.render_capability_facts_block(facts)
        self.assertIn("8 CPU cores", block)
        self.assertIn("no discrete GPU", block)
        self.assertIn("never invent hardware", block)  # instructs the model, doesn't write the prose

    def test_light_repo_big_host_recommends_local(self):
        from duckln import capability_facts as cf

        with patch.object(cf, "active_repo_resource_facts", return_value=("Web", False, False)), \
             patch("duckln.resource_manager.probe_host_resources", return_value=_snap(gpu=False, ram_mb=32768)):
            facts = cf.gather_capability_facts("/tmp")
        self.assertEqual(facts["recommended_target"], "local")

    def test_heavy_repo_low_ram_recommends_bigger_vm(self):
        from duckln import capability_facts as cf

        with patch.object(cf, "active_repo_resource_facts", return_value=("Mono", False, True)), \
             patch("duckln.resource_manager.probe_host_resources", return_value=_snap(gpu=False, ram_mb=8192)):
            facts = cf.gather_capability_facts("/tmp")
        self.assertEqual(facts["recommended_target"], "bigger-vm")
        self.assertIn("ram_below_16gb", facts["reasons"])

    def test_no_active_repo_no_signal(self):
        from duckln import capability_facts as cf

        self.assertEqual(cf.active_repo_resource_facts(tempfile.mkdtemp()), (None, False, False))


class F2GroundingInjection(unittest.TestCase):
    def _ctx(self):
        return SimpleNamespace(
            durable_recommendation=SimpleNamespace(primary_repo="X", secondary_repo=None, alternatives=()),
            pending_offer=SimpleNamespace(kind=""),
            execution_target="local", active_vm_name=None,
            followup_state=SimpleNamespace(last_route_family=None),
            durable_repo_memory=None,
        )

    def test_capability_route_gets_facts(self):
        import duckln.capability_facts as cf
        from duckln.context_assembler import assemble_supervisor_prompt_context

        with tempfile.TemporaryDirectory() as d, \
             patch.object(cf, "capability_facts_block_for", return_value="MACHINE_FACTS_MARKER"):
            out = assemble_supervisor_prompt_context(config_dir=Path(d), route_family="capability", context=self._ctx())
        self.assertTrue(any("MACHINE_FACTS_MARKER" in b for b in out.context_blocks))

    def test_recommendation_route_gets_facts(self):
        import duckln.capability_facts as cf
        from duckln.context_assembler import assemble_supervisor_prompt_context

        with tempfile.TemporaryDirectory() as d, \
             patch.object(cf, "capability_facts_block_for", return_value="MACHINE_FACTS_MARKER"):
            out = assemble_supervisor_prompt_context(
                config_dir=Path(d), route_family="repo_recommendation_single", context=self._ctx())
        self.assertTrue(any("MACHINE_FACTS_MARKER" in b for b in out.context_blocks))

    def test_plain_small_talk_does_not_get_facts(self):
        import duckln.capability_facts as cf
        from duckln.context_assembler import assemble_supervisor_prompt_context

        with tempfile.TemporaryDirectory() as d, \
             patch.object(cf, "capability_facts_block_for", return_value="MACHINE_FACTS_MARKER"):
            out = assemble_supervisor_prompt_context(config_dir=Path(d), route_family="small_talk", context=self._ctx())
        self.assertFalse(any("MACHINE_FACTS_MARKER" in b for b in out.context_blocks))


class F2bPolishExplainsWhy(unittest.TestCase):
    def test_polish_relaxed_for_why_families(self):
        # The polish prompt must let the model EXPLAIN the 'why' (grounded) for recommendation
        # routes, while staying strict elsewhere.
        src = Path("src/duckln/conversation_routes/provider_support.py").read_text(encoding="utf-8")
        self.assertIn("Plan 185", src)
        self.assertIn("explain the recommendation and the WHY", src)
        self.assertIn("repo_recommendation_rationale", src)


class F3NoHardcodedHandler(unittest.TestCase):
    def test_plan184_capability_handler_removed(self):
        src = Path("src/duckln/main.py").read_text(encoding="utf-8")
        self.assertNotIn("def _maybe_answer_capability_query", src)
        self.assertNotIn("def _interactive_repo_recommendation", src)
        self.assertIn("flow to the LLM conversation", src)  # the routing note


if __name__ == "__main__":
    unittest.main()
