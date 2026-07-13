from __future__ import annotations

import unittest

from duckln.main import _chat_supports_thoughts, _emit_thought


class _FakeChatWithThoughts:
    def __init__(self):
        self.thoughts: list[str] = []

    def add_thought(self, text: str) -> None:
        self.thoughts.append(text)


class _PlainChat:
    pass


class TestThoughtsBox(unittest.TestCase):
    def test_supports_thoughts_detection(self):
        self.assertTrue(_chat_supports_thoughts(_FakeChatWithThoughts()))
        self.assertFalse(_chat_supports_thoughts(_PlainChat()))
        self.assertFalse(_chat_supports_thoughts(None))

    def test_routes_to_add_thought_when_supported(self):
        chat = _FakeChatWithThoughts()
        _emit_thought(chat, "Detected family: node_typescript")
        self.assertEqual(chat.thoughts, ["Detected family: node_typescript"])

    def test_redacts_before_routing(self):
        chat = _FakeChatWithThoughts()
        _emit_thought(chat, "using token sk-ABC123DEF456GHI789JKL to clone")
        self.assertEqual(len(chat.thoughts), 1)
        self.assertNotIn("sk-ABC123DEF456GHI789JKL", chat.thoughts[0])

    def test_fallback_to_display_in_plain_cli(self):
        out: list[str] = []
        _emit_thought(_PlainChat(), "Chosen run command: npm run dev", display=out.append)
        self.assertEqual(len(out), 1)
        self.assertIn("npm run dev", out[0])
        self.assertTrue(out[0].startswith("·"))

    def test_blank_thought_is_dropped(self):
        chat = _FakeChatWithThoughts()
        _emit_thought(chat, "   ")
        self.assertEqual(chat.thoughts, [])


if __name__ == "__main__":
    unittest.main()
