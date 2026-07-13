"""Tests for Duckln banner rendering."""

from __future__ import annotations

import io
import re
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from duckln import __version__
from duckln.render_blocks import action_block, file_map_block, join_blocks, paragraph_block, status_block, step_list_block
from duckln.ui import (
    ChatMessage,
    _fallback_render_chat_message,
    _fallback_transcript_lines,
    build_chat_interface,
    build_chat_input,
    COMPACT_BANNER,
    DUCKLN_COMMAND,
    DUCKLN_GOLD,
    DUCKLN_GREY,
    DUCKLN_SYSTEM,
    DUCKLN_USER_INPUT,
    build_terminal_display,
    build_terminal_input,
    load_banner_asset,
    render_banner,
    render_first_run_safety_panel,
    render_input_footer,
    render_input_prompt,
    render_with_commands,
    render_session_header,
    render_status_box,
)


class BannerRenderingTest(unittest.TestCase):
    @staticmethod
    def _strip_color_and_markup(text: str) -> str:
        no_ansi = re.sub(r"\x1b\[[0-9;]*m", "", text)
        return re.sub(r"\[[#A-Za-z0-9/]+\]", "", no_ansi)

    def test_full_banner_is_loaded_from_asset(self) -> None:
        banner = load_banner_asset()

        self.assertIn("Duckln", banner)
        self.assertIn("AI Terminal Mentor", banner)

    def test_full_banner_is_used_when_width_is_sufficient(self) -> None:
        rendered = render_banner(width=120)

        self.assertIn("██████╗", rendered)
        self.assertTrue(DUCKLN_GOLD in rendered or "██████╗" in rendered)
        self.assertNotIn(COMPACT_BANNER, rendered)

    def test_compact_banner_is_used_for_narrow_terminals(self) -> None:
        rendered = render_banner(width=20)

        self.assertIn(COMPACT_BANNER, rendered)
        self.assertNotIn("██████╗", rendered)

    def test_session_header_uses_command_and_brand_styles(self) -> None:
        rendered = render_session_header(
            provider="OpenAI",
            model="gpt-4o-mini",
            mode="HOTL",
            user_name="Shreyas",
            memory_state="ready",
            width=80,
        )

        plain = re.sub(r"\x1b\[[0-9;]*m", "", rendered)
        self.assertIn("provider : OpenAI", plain)
        self.assertIn("/provider to change", plain)
        self.assertIn(f"◆ Duckln (v{__version__})", plain)
        self.assertIn("\x1b[38;2;245;166;35m◆ Duckln", rendered)
        self.assertIn("\x1b[38;2;169;169;169mprovider", rendered)
        self.assertIn("\x1b[38;2;0;255;255m/provider", rendered)
        self.assertIn("\x1b[38;2;169;169;169m to change", rendered)
        self.assertTrue(DUCKLN_GOLD in rendered or "◆ Duckln" in rendered)
        self.assertTrue(DUCKLN_SYSTEM in rendered or "provider" in rendered)

    def test_session_header_stacks_hints_cleanly_on_narrow_terminals(self) -> None:
        rendered = render_session_header(
            provider="OpenRouter",
            model="ai21/jamba-large-1.7",
            mode="HOOTLWO",
            user_name="Shreyas",
            memory_state="ready",
            width=40,
        )

        plain_lines = [
            re.sub(r"\x1b\[[0-9;]*m", "", line).strip("│ ").rstrip()
            for line in rendered.splitlines()
        ]
        provider_line = next(line for line in plain_lines if "provider" in line)
        brand_line = next(line for line in plain_lines if f"v{__version__}" in line)
        command_line = next(line for line in plain_lines if "/provider to change" in line)
        self.assertEqual(f"◆ Duckln (v{__version__})", brand_line.strip())
        self.assertNotIn("/provider", provider_line)
        self.assertTrue(command_line.strip().startswith("/provider"))

    def test_session_header_keeps_blank_gap_between_brand_row_and_provider_row(self) -> None:
        rendered = render_session_header(
            provider="OpenRouter",
            model="ai21/jamba-large-1.7",
            mode="HOOTLWO",
            user_name="Shreyas",
            memory_state="ready",
            width=80,
        )

        plain_lines = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in rendered.splitlines()]
        brand_index = next(index for index, line in enumerate(plain_lines) if "Duckln" in line)
        provider_index = next(index for index, line in enumerate(plain_lines) if "provider" in line)
        spacer_lines = plain_lines[brand_index + 1 : provider_index]
        self.assertTrue(any(not line.strip("│ ").strip() for line in spacer_lines))

    def test_first_run_safety_panel_uses_duckln_brand_color(self) -> None:
        rendered = render_first_run_safety_panel()

        plain = self._strip_color_and_markup(rendered)
        self.assertIn("◆ Duckln Safety & Permissions", plain)
        self.assertTrue("\x1b[38;2;245;166;35m◆ Duckln" in rendered or "[#F5A623]◆ Duckln" in rendered)

    def test_terminal_input_keeps_prompt_and_entered_text_in_user_input_color(self) -> None:
        prompt_input = build_terminal_input()
        captured_stdout = io.StringIO()

        with patch("builtins.input", return_value="/help") as mocked_input, redirect_stdout(captured_stdout):
            entered = prompt_input("duckln> ")

        self.assertEqual("/help", entered)
        self.assertEqual(
            "\x1b[38;2;245;166;35mduckln>\x1b[38;2;255;255;255m ",
            mocked_input.call_args.args[0],
        )
        self.assertEqual("\x1b[0m", captured_stdout.getvalue())

    def test_render_input_prompt_uses_gold_prompt_and_white_input_color(self) -> None:
        prompt = render_input_prompt("duckln> ")

        self.assertIn("\x1b[38;2;245;166;35mduckln>", prompt)
        self.assertTrue(prompt.endswith("\x1b[38;2;255;255;255m "))

    def test_render_input_footer_uses_command_user_and_system_colors(self) -> None:
        footer = render_input_footer()

        self.assertIn("/help", footer)
        self.assertIn("Enter", footer)
        self.assertIn("Ctrl+C", footer)
        self.assertTrue(DUCKLN_COMMAND in footer or "\x1b[38;2;0;255;255m/help" in footer)
        self.assertTrue(DUCKLN_USER_INPUT in footer or "\x1b[38;2;255;255;255mEnter" in footer)
        self.assertTrue(DUCKLN_SYSTEM in footer or "to send" in footer)
        self.assertTrue(DUCKLN_GREY in footer or " • " in footer)

    def test_render_input_footer_can_include_execution_and_usage_hints(self) -> None:
        footer = render_input_footer(execution_label="Docker:whisper", usage_summary="tokens 10/5/15")

        self.assertIn("Docker:whisper", footer)
        self.assertIn("tokens 10/5/15", footer)

    def test_render_with_commands_only_styles_the_command_token(self) -> None:
        rendered = render_with_commands("Type /help for commands.", base_color=DUCKLN_SYSTEM)

        self.assertIn("\x1b[38;2;0;255;255m/help\x1b[0m", rendered)
        self.assertIn("\x1b[38;2;169;169;169mType ", rendered)
        self.assertIn("\x1b[38;2;169;169;169m for commands.", rendered)

    def test_fallback_transcript_adds_blank_line_after_assistant_blocks(self) -> None:
        lines = _fallback_transcript_lines(
            [
                ChatMessage(role="assistant", content="First paragraph.\n\n- item one\n- item two"),
                ChatMessage(role="user", content="next"),
            ],
            width=60,
            height=20,
        )

        plain_lines = [self._strip_color_and_markup(line) for line in lines]
        self.assertIn("", plain_lines)

    def test_fallback_assistant_and_user_bodies_share_white_transcript_color(self) -> None:
        assistant_lines = _fallback_render_chat_message(ChatMessage(role="assistant", content="Duckln is ready."), width=60)
        user_lines = _fallback_render_chat_message(ChatMessage(role="user", content="hi there"), width=60)

        self.assertIn("\x1b[38;2;255;255;255mDuckln is ready.\x1b[0m", assistant_lines[0])
        self.assertIn("\x1b[38;2;255;255;255mhi there\x1b[0m", user_lines[0])
        self.assertIn("\x1b[38;2;245;166;35m● ", assistant_lines[0])

    def test_render_status_box_is_compact_and_keeps_message_readable(self) -> None:
        rendered = render_status_box("Reading your README...")

        plain = re.sub(r"\x1b\[[0-9;]*m", "", rendered)
        self.assertIn("Thinking…", plain)
        self.assertIn("Reading your README...", plain)
        self.assertIn("┌", plain)
        self.assertIn("└", plain)

    def test_chat_interface_keeps_transcript_messages_in_memory(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        captured_stdout = io.StringIO()
        with redirect_stdout(captured_stdout):
            chat.start()
            chat.display("Duckln is ready.")
            chat.display(render_status_box("Reading your README..."))

        self.assertEqual(("assistant", "status"), tuple(message.role for message in chat.messages))
        self.assertIn("Duckln is ready.", chat.messages[0].content)
        rendered = captured_stdout.getvalue()
        plain = self._strip_color_and_markup(rendered)
        self.assertIn("Reading your README...", plain)
        self.assertNotIn("┌ Thinking…", rendered)

    def test_chat_interface_preserves_multiline_assistant_message_blocks(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        captured_stdout = io.StringIO()
        with redirect_stdout(captured_stdout):
            chat.start()
            chat.display("/help — Show the available slash commands.\n\t/mode — Change how much Duckln can do for you.")

        plain = self._strip_color_and_markup(captured_stdout.getvalue())
        self.assertIn("● /help — Show the available slash commands.", plain)
        self.assertIn("/mode — Change how much Duckln can do for you.", plain)

    def test_chat_interface_keeps_blank_lines_between_assistant_paragraphs(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        captured_stdout = io.StringIO()
        with redirect_stdout(captured_stdout):
            chat.start()
            chat.display("First block.\n\nSecond block.")

        plain_lines = self._strip_color_and_markup(captured_stdout.getvalue()).splitlines()
        first_index = next(index for index, line in enumerate(plain_lines) if "First block." in line)
        second_index = next(index for index, line in enumerate(plain_lines) if "Second block." in line)
        self.assertGreaterEqual(second_index - first_index, 2)

    def test_terminal_chat_interface_can_open_managed_browser_window(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")

        with patch("duckln.ui.ManagedBrowserWindow.open", return_value=True) as open_mock:
            opened = chat.open_web_preview(url="http://localhost:3000", title_hint="open-webui")

        self.assertTrue(opened)
        open_mock.assert_called_once_with(url="http://localhost:3000", title_hint="open-webui")

    def test_chat_interface_wraps_bullet_lists_readably(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        captured_stdout = io.StringIO()
        with redirect_stdout(captured_stdout):
            chat.start()
            chat.display("Repos I can see:\n\n- private-gpt\n- text-generation-webui")

        plain = self._strip_color_and_markup(captured_stdout.getvalue())
        self.assertIn("● Repos I can see:", plain)
        self.assertIn("- private-gpt", plain)
        self.assertIn("- text-generation-webui", plain)

    def test_chat_interface_compacts_repair_flow_into_single_evolving_transcript_entry(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        captured_stdout = io.StringIO()
        with redirect_stdout(captured_stdout):
            chat.start()
            chat.update_objective_status("openclaw • runtime repair", objective_key="runtime_repair:openclaw")
            chat.display("Duckln checked current official docs for this blocker: openclaw compose environment variables.")
            chat.display(
                "Duckln trace: shell command execution.\n"
                "1. Tool: Controlled shell runner\n"
                "2. Action: Run docker compose up"
            )
            chat.display(render_status_box("Starting openclaw from Duckln’s managed workspace...", title="Supervisor agent"))

        self.assertEqual(1, len(chat.messages))
        self.assertEqual("assistant", chat.messages[0].role)
        content = str(chat.messages[0].content)
        self.assertIn("Active repair", content)
        self.assertIn("Current step: Starting openclaw from Duckln’s managed workspace...", content)
        self.assertIn("Official docs checked", content)
        self.assertIn("shell command execution", content)
        self.assertIn("Starting openclaw from Duckln’s managed workspace...", content)

        plain = self._strip_color_and_markup(captured_stdout.getvalue())
        self.assertIn("Active repair", plain)
        self.assertIn("Recent repair updates:", plain)

    def test_chat_interface_starts_fresh_repair_surface_when_objective_changes(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        with redirect_stdout(io.StringIO()):
            chat.start()
            chat.update_objective_status("openclaw • runtime repair", objective_key="runtime_repair:openclaw")
            chat.display("Duckln checked current official docs for this blocker: openclaw compose environment variables.")
            chat.update_objective_status("whisper • runtime repair", objective_key="runtime_repair:whisper")
            chat.display("Supervisor agent installed the missing prerequisite ffmpeg and will re-check whisper now.")

        self.assertEqual(2, len(chat.messages))
        first = str(chat.messages[0].content)
        second = str(chat.messages[1].content)
        self.assertIn("Official docs checked", first)
        self.assertNotIn("ffmpeg", first)
        self.assertIn("ffmpeg", second)
        self.assertNotIn("Official docs checked", second)

    def test_chat_interface_keeps_same_repair_surface_across_target_shift_for_same_objective(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        with redirect_stdout(io.StringIO()):
            chat.start()
            chat.update_objective_status("openclaw • runtime repair • local", objective_key="runtime_repair:openclaw")
            chat.display("Duckln checked current official docs for this blocker: openclaw compose environment variables.")
            chat.update_objective_status("openclaw • runtime repair • docker", objective_key="runtime_repair:openclaw")
            chat.display(
                "Duckln trace: shell command execution.\n"
                "1. Tool: Controlled shell runner\n"
                "2. Action: Run docker compose up"
            )

        self.assertEqual(1, len(chat.messages))
        content = str(chat.messages[0].content)
        self.assertIn("Official docs checked", content)
        self.assertIn("shell command execution", content)

    def test_chat_interface_keeps_unrelated_trace_as_regular_transcript_entry(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        captured_stdout = io.StringIO()
        with redirect_stdout(captured_stdout):
            chat.start()
            chat.display(
                "Duckln trace: shell command execution.\n"
                "1. Tool: Controlled shell runner\n"
                "2. Action: Run docker compose up"
            )

        self.assertEqual(1, len(chat.messages))
        self.assertIn("Duckln trace: shell command execution.", str(chat.messages[0].content))
        plain = self._strip_color_and_markup(captured_stdout.getvalue())
        self.assertIn("Duckln trace: shell command execution.", plain)
        self.assertNotIn("Active repair", plain)

    def test_display_step_renders_checkmark_glyphs_inline(self) -> None:
        from duckln.ui import render_step_line

        running = self._strip_color_and_markup(render_step_line("Launch VM"))
        done = self._strip_color_and_markup(render_step_line("Launch VM", status="done", duration_seconds=12.3))
        failed = self._strip_color_and_markup(render_step_line("Launch VM", status="failed", detail="daemon down"))

        self.assertTrue(running.startswith("◐"))
        self.assertIn("Launch VM", running)
        self.assertTrue(done.startswith("✓"))
        self.assertIn("(12.3s)", done)
        self.assertTrue(failed.startswith("✗"))
        self.assertIn("daemon down", failed)

    def test_display_step_updates_in_place_for_same_step_id(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        with redirect_stdout(io.StringIO()):
            chat.start()
            chat.display_step("vm.launch", "Launch VM")
            chat.display_step("vm.launch", "Launch VM", status="done", duration_seconds=4.5)

        # Single message slot, updated in place — not two messages.
        self.assertEqual(1, len(chat.messages))
        rendered = self._strip_color_and_markup(str(chat.messages[0].content))
        self.assertTrue(rendered.startswith("✓"))
        self.assertIn("(4.5s)", rendered)

    def test_chat_interface_renders_typed_blocks_readably(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        captured_stdout = io.StringIO()
        with redirect_stdout(captured_stdout):
            chat.start()
            chat.display(
                join_blocks(
                    paragraph_block("Repo summary."),
                    action_block("Next step", ("Inspect requirements", "Run the health check")),
                    file_map_block("Files", (("README", "/tmp/README.md"),)),
                )
            )

        plain = self._strip_color_and_markup(captured_stdout.getvalue())
        self.assertIn("Repo summary.", plain)
        self.assertIn("- Inspect requirements", plain)
        self.assertIn("- README: /tmp/README.md", plain)

    def test_chat_interface_wraps_numbered_step_lists_readably(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        captured_stdout = io.StringIO()
        with redirect_stdout(captured_stdout):
            chat.start()
            chat.display(
                join_blocks(
                    paragraph_block("Smallest bounded setup path:"),
                    step_list_block(
                        (
                            "Create project virtualenv: python -m venv .venv",
                            "Install repo requirements with the project interpreter so the environment stays isolated and repeatable.",
                        )
                    ),
                    status_block("Plan saved", "/tmp/alpha.todo.md"),
                )
            )

        plain = self._strip_color_and_markup(captured_stdout.getvalue())
        self.assertIn("Smallest bounded setup path:", plain)
        self.assertIn("1. Create project virtualenv: python -m venv .venv", plain)
        self.assertIn("2. Install repo requirements", plain)
        self.assertIn("Plan saved", plain)
        self.assertIn("/tmp/alpha.todo.md", plain)

    def test_chat_interface_prompt_records_user_message(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        captured_stdout = io.StringIO()
        with redirect_stdout(captured_stdout):
            chat.start()
            entered = chat.prompt("", input_func=lambda prompt: "/help")

        self.assertEqual("/help", entered)
        self.assertEqual("Shreyas", chat.messages[-1].role)
        self.assertEqual("/help", chat.messages[-1].content)
        rendered = captured_stdout.getvalue()
        self.assertNotIn("duckln>", rendered)

    def test_build_chat_input_uses_prompt_session_placeholder(self) -> None:
        with patch("builtins.input", return_value="hello from box") as mocked_input:
            prompt_input = build_chat_input()
            entered = prompt_input("")

        self.assertEqual("hello from box", entered)
        mocked_input.assert_called_once_with("\x1b[38;2;255;255;255m> ")

    def test_chat_interface_uses_stable_non_live_renderer(self) -> None:
        chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")
        self.assertFalse(chat.supports_live)

    def test_build_chat_interface_sets_footer_for_split_pane_adapter(self) -> None:
        class FakeSplitPaneChat:
            def __init__(
                self,
                *,
                session_header: str,
                user_name: str,
                initial_connection_type: str = "local",
                config_dir=None,
            ) -> None:
                self.session_header = session_header
                self.user_name = user_name
                self.initial_connection_type = initial_connection_type
                self.config_dir = config_dir
                self.footer_text = None

            def set_footer_text(self, footer_text: str) -> None:
                self.footer_text = footer_text

        fake_stdin = SimpleNamespace(isatty=lambda: True)
        fake_stdout = SimpleNamespace(isatty=lambda: True)
        with patch("duckln.ui.SplitPaneChatInterface", FakeSplitPaneChat), patch(
            "duckln.ui.split_pane_dependencies_available", return_value=True
        ), patch("duckln.ui.sys.stdin", fake_stdin), patch("duckln.ui.sys.stdout", fake_stdout):
            chat = build_chat_interface(session_header="HEADER", user_name="Shreyas")

        self.assertEqual(render_input_footer(), chat.footer_text)

    def test_terminal_display_renders_system_text_dim_grey_and_commands_cyan(self) -> None:
        captured_stdout = io.StringIO()

        with patch("duckln.ui.Console", None), redirect_stdout(captured_stdout):
            display = build_terminal_display()
            display("/help — Show the available slash commands.")

        rendered = captured_stdout.getvalue()
        self.assertIn("\x1b[38;2;0;255;255m/help\x1b[0m", rendered)
        self.assertIn("\x1b[38;2;169;169;169m — Show the available slash commands.\x1b[0m", rendered)
        self.assertTrue(DUCKLN_COMMAND in rendered or "/help" in rendered)
        self.assertTrue(DUCKLN_SYSTEM in rendered or "Show the available slash commands." in rendered)


if __name__ == "__main__":
    unittest.main()
