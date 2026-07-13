from __future__ import annotations

import unittest
from types import SimpleNamespace

from duckln.harness.context_budget import (
    assemble_observation_block,
    context_window_for,
    estimate_tokens,
    map_reduce_summarize,
    usable_context_tokens,
)


def _obs(turn, tool, args, ok=True, payload=None, err=""):
    return SimpleNamespace(turn=turn, tool=tool, args=args, ok=ok, payload=payload, error_code=err, error_message=err)


class TestTokenBudget(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertGreaterEqual(estimate_tokens("a" * 400), 100)

    def test_model_windows(self):
        self.assertEqual(context_window_for("claude-opus-4"), 200_000)
        self.assertEqual(context_window_for("gemma2:2b"), 8_192)
        self.assertEqual(context_window_for("unknown-model"), 8_192)

    def test_usable_reserves_headroom(self):
        self.assertLess(usable_context_tokens("gemma2:2b"), context_window_for("gemma2:2b"))


class TestCompaction(unittest.TestCase):
    def test_recent_verbatim_older_summarized(self):
        obs = [_obs(i, "fs.read_file", {"path": f"f{i}.py"}, payload={"text": f"content{i}"}) for i in range(12)]
        block = assemble_observation_block(obs, live_window=3)
        # older folded into a compacted summary
        self.assertIn("Earlier findings", block)
        # the 3 most recent appear verbatim with payloads
        self.assertIn("content11", block)
        self.assertIn("Recent observations", block)
        # an old payload is NOT shown verbatim (only summarized)
        self.assertNotIn("content0", block)

    def test_dedup_consecutive(self):
        obs = [_obs(1, "shell.probe", {"command": "ls"}), _obs(2, "shell.probe", {"command": "ls"}), _obs(3, "fs.read_file", {"path": "a"})]
        block = assemble_observation_block(obs, live_window=8)
        self.assertEqual(block.count("shell.probe"), 1)  # the duplicate collapsed

    def test_summarizer_used_for_old(self):
        calls = {"n": 0}

        def summ(_text):
            calls["n"] += 1
            return "SUMMARY"

        obs = [_obs(i, "fs.read_file", {"path": f"f{i}"}) for i in range(10)]
        block = assemble_observation_block(obs, live_window=2, summarizer=summ)
        self.assertIn("SUMMARY", block)
        self.assertGreaterEqual(calls["n"], 1)


class TestMapReduce(unittest.TestCase):
    def test_small_text_passthrough(self):
        self.assertEqual(map_reduce_summarize("short", chunk_chars=100), "short")

    def test_large_text_chunked_and_query_biased(self):
        text = "\n".join(f"line {i} foo" if i == 500 else f"line {i}" for i in range(2000))
        out = map_reduce_summarize(text, query="foo", chunk_chars=4000)
        self.assertIn("chunk", out.lower())
        self.assertIn("foo", out)  # the query-relevant line survived map

    def test_summarizer_maps_each_chunk(self):
        text = "x" * 20000
        out = map_reduce_summarize(text, chunk_chars=5000, summarizer=lambda t: "S")
        self.assertIn("S", out)
        self.assertLess(len(out), len(text))  # compacted

    def test_summarizer_reduce_when_mapped_large(self):
        text = "x" * 60000
        out = map_reduce_summarize(text, chunk_chars=4000, summarizer=lambda t: "Z" * 5000)
        # mapped result exceeds chunk_chars → final reduce produces one summary
        self.assertTrue(out.startswith("Z"))


if __name__ == "__main__":
    unittest.main()
