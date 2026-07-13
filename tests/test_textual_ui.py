"""Tests for optional Textual split-pane UI scaffolding."""

from __future__ import annotations

import tempfile
import time
import unittest
from unittest.mock import patch

from duckln.textual_ui import (
    AWS_ORANGE,
    DOCKER_CYAN,
    DUCKLN_EGGSHELL,
    GCP_BLUE,
    LOCAL_GREEN,
    MLTerminalWatcher,
    SplitPaneChatInterface,
    TEXTUAL_SPLIT_PANE_CSS,
    VM_GOLD,
    _attachment_button_label,
    _choice_activity_text,
    _extract_terminal_urls,
    _extract_slash_command_from_label,
    _is_repair_adjacent_trace_title,
    _normalize_attachment_path,
    _looks_like_heading,
    _objective_chat_significance,
    _parse_trace_message,
    _read_os_clipboard_text,
    _is_repair_trace_title,
    _repair_session_message_payload,
    _render_progress_bar,
    _slash_command_label,
    _slash_command_suggestions,
    _status_payload_from_box,
    _strip_ansi,
    _terminal_incident_activity_update,
    _transient_activity_update,
    build_connection_context_label,
    default_shell_command,
    detect_terminal_backend,
    infer_terminal_connection_from_output,
    _wrap_chat_message,
    _overlay_choice_label,
    _render_wrapped_role_lines,
    _resolve_overlay_choice,
    split_pane_dependencies_available,
    split_pane_unavailability_message,
)
from duckln.repair_intake import DependencyApprovalItem, DependencyApprovalRequest


class PaneSentinelWaiterTest(unittest.TestCase):
    def test_terminal_incident_activity_pauses_on_permission_error(self) -> None:
        text, spinner = _terminal_incident_activity_update(
            {"summary": "npm error EACCES: permission denied, mkdir '/usr/lib/node_modules/openclaw'"}
        )

        self.assertIn("Paused", text)
        self.assertFalse(spinner)

    def test_terminal_watcher_detects_app_yes_no_prompt(self) -> None:
        watcher = MLTerminalWatcher(lambda _message: None)
        try:
            watcher._recent_lines.append("I understand this is personal-by-default.")

            observation, incident = watcher.analyse_output("Continue? Yes / No")
        finally:
            watcher.stop()

        self.assertEqual("Duckln noticed the app is waiting for input in the terminal pane.", observation)
        self.assertIsNotNone(incident)
        self.assertEqual("app_prompt_active", incident["category"])
        self.assertIn("Continue? Yes/No", incident["summary"])

    def test_waiter_captures_lines_until_sentinel_then_returns_exit_code(self) -> None:
        from duckln.textual_ui import PaneSentinelWaiter

        waiter = PaneSentinelWaiter()
        waiter.begin("abc123")
        waiter.submit_line("hello world")
        waiter.submit_line("\x1b[32mcolored line\x1b[0m")
        waiter.submit_line("DUCKLN-DONE-abc123:0")

        completed, captured, exit_code = waiter.wait("abc123", timeout=1.0)

        self.assertTrue(completed)
        self.assertEqual(0, exit_code)
        # ANSI escapes are stripped before capture.
        self.assertIn("hello world", captured)
        self.assertIn("colored line", captured)
        # Sentinel line is consumed, not captured as output.
        self.assertFalse(any("DUCKLN-DONE" in line for line in captured))

    def test_waiter_signals_timeout_when_sentinel_never_arrives(self) -> None:
        from duckln.textual_ui import PaneSentinelWaiter

        waiter = PaneSentinelWaiter()
        waiter.begin("never")
        waiter.submit_line("partial line")

        completed, captured, exit_code = waiter.wait("never", timeout=0.1)

        self.assertFalse(completed)
        self.assertEqual(-1, exit_code)
        self.assertIn("partial line", captured)


    def test_waiter_isolates_concurrent_sentinels(self) -> None:
        from duckln.textual_ui import PaneSentinelWaiter

        waiter = PaneSentinelWaiter()
        waiter.begin("alpha")
        waiter.begin("beta")
        waiter.submit_line("first")
        waiter.submit_line("DUCKLN-DONE-alpha:0")
        waiter.submit_line("second")
        waiter.submit_line("DUCKLN-DONE-beta:7")

        ok_a, captured_a, code_a = waiter.wait("alpha", timeout=1.0)
        ok_b, captured_b, code_b = waiter.wait("beta", timeout=1.0)

        self.assertTrue(ok_a and ok_b)
        self.assertEqual(0, code_a)
        self.assertEqual(7, code_b)
        # alpha sees only the line before its sentinel; beta sees both.
        self.assertEqual(["first"], captured_a)
        self.assertEqual(["first", "second"], captured_b)


class TerminalCssTest(unittest.TestCase):
    def test_terminal_pane_and_shell_allow_vertical_scroll(self) -> None:
        self.assertIn("#terminal-pane", TEXTUAL_SPLIT_PANE_CSS)
        self.assertIn("#terminal-frame", TEXTUAL_SPLIT_PANE_CSS)
        self.assertIn("#terminal-shell", TEXTUAL_SPLIT_PANE_CSS)
        self.assertIn("#terminal-scroll-indicator", TEXTUAL_SPLIT_PANE_CSS)
        self.assertGreaterEqual(TEXTUAL_SPLIT_PANE_CSS.count("overflow-y: auto;"), 2)


class TextualUiScaffoldingTest(unittest.TestCase):
    def test_detect_terminal_backend_prefers_ptyprocess_on_posix(self) -> None:
        with patch("duckln.textual_ui.importlib_util.find_spec", return_value=object()):
            backend = detect_terminal_backend("Darwin")

        self.assertEqual("ptyprocess", backend.backend_package)
        self.assertTrue(backend.backend_available)

    def test_detect_terminal_backend_prefers_pywinpty_on_windows(self) -> None:
        with patch("duckln.textual_ui.importlib_util.find_spec", return_value=object()):
            backend = detect_terminal_backend("Windows")

        self.assertEqual("pywinpty", backend.backend_package)
        self.assertTrue(backend.backend_available)

    def test_split_pane_dependencies_report_missing_stack_cleanly(self) -> None:
        with patch("duckln.textual_ui.importlib_util.find_spec", return_value=None):
            self.assertFalse(split_pane_dependencies_available("Darwin"))
            self.assertIn("textual", split_pane_unavailability_message("Darwin"))

    def test_connection_context_labels_match_target_colors(self) -> None:
        local = build_connection_context_label(connection_type="local")
        vm = build_connection_context_label(connection_type="vm", vm_name="Multipass Ubuntu")
        aws = build_connection_context_label(connection_type="aws", cloud_region="us-east-1", cloud_shape="t4g.xlarge")
        gcp = build_connection_context_label(connection_type="gcp", cloud_region="europe-west4", cloud_shape="n1-standard-4")
        docker = build_connection_context_label(connection_type="docker", docker_name="container")

        # Plan 110 Fix 4: composite location-resource labels.
        self.assertEqual(("local", LOCAL_GREEN), (local.label, local.color))
        self.assertEqual(("local-Multipass Ubuntu", VM_GOLD), (vm.label, vm.color))
        self.assertEqual(("aws-t4g.xlarge", AWS_ORANGE), (aws.label, aws.color))
        self.assertEqual(("gcp-n1-standard-4", GCP_BLUE), (gcp.label, gcp.color))
        self.assertEqual(("local-container", DOCKER_CYAN), (docker.label, docker.color))

    def test_user_transcript_color_is_plain_white(self) -> None:
        self.assertEqual("#FFFFFF", DUCKLN_EGGSHELL)

    def test_default_shell_command_uses_platform_specific_defaults(self) -> None:
        with patch("duckln.textual_ui.platform.system", return_value="Windows"):
            self.assertTrue(default_shell_command().lower().endswith("cmd.exe"))
        with patch("duckln.textual_ui.platform.system", return_value="Darwin"), patch.dict("duckln.textual_ui.os.environ", {"SHELL": "/bin/zsh"}):
            self.assertEqual("/bin/zsh -l", default_shell_command())

    def test_ml_terminal_watcher_detects_error_and_runtime_signals(self) -> None:
        observations: list[str] = []
        watcher = MLTerminalWatcher(observations.append)
        try:
            observation, incident = watcher.analyse_output("Traceback (most recent call last):")
            self.assertIn("error", str(observation).lower())
            self.assertEqual("runtime_failure", incident["category"])
            whisper_observation, whisper_incident = watcher.analyse_output("whisper --help")
            self.assertIn("whisper", str(whisper_observation).lower())
            self.assertIsNone(whisper_incident)
            yolo_observation, yolo_incident = watcher.analyse_output("Ultralytics YOLOv8")
            self.assertIn("yolo", str(yolo_observation).lower())
            self.assertIsNone(yolo_incident)
            self.assertEqual((None, None), watcher.analyse_output("plain shell prompt"))
        finally:
            watcher.stop()

    def test_ml_terminal_watcher_detects_quoted_ubuntu_command_not_found(self) -> None:
        observations: list[str] = []
        watcher = MLTerminalWatcher(observations.append)
        try:
            observation, incident = watcher.analyse_output("Command 'multipass' not found, but can be installed with:")

            self.assertIn("error", str(observation).lower())
            assert incident is not None
            self.assertEqual("missing_command", incident["category"])
            self.assertIn("multipass", str(incident["summary"]).lower())
            self.assertEqual("multipass", incident["package_hint"])
            self.assertIn("Command 'multipass' not found", incident["fatal_line"])
        finally:
            watcher.stop()

    def test_split_pane_status_text_is_compact_and_structured(self) -> None:
        if SplitPaneChatInterface is None:
            self.skipTest("optional split pane dependencies unavailable")
        header = (
            "│ ◆ Duckln (v0.1.0)\n"
            "│ provider : OpenAI\n"
            "│ model    : gpt-4o-mini\n"
            "│ mode     : HOTL\n"
            "│ user     : Shreyas\n"
        )
        chat = SplitPaneChatInterface(session_header=header, user_name="Shreyas")
        text = chat._status_text()
        self.assertIn("provider OpenAI", text)
        self.assertIn("model gpt-4o-mini", text)
        self.assertIn("mode HOTL", text)
        self.assertIn("user Shreyas", text)

    def test_split_pane_status_text_uses_composite_connection_label(self) -> None:
        # Plan 110 Fix 4: the heading now shows the precise `local-<vmname>` so the
        # user always knows WHERE things run.
        if SplitPaneChatInterface is None:
            self.skipTest("optional split pane dependencies unavailable")
        chat = SplitPaneChatInterface(session_header="provider : Ollama\nmodel : gemma2\nmode : HOOTLWO\nuser : Shreyas\n", user_name="Shreyas")

        chat.update_connection(connection_type="vm", vm_name="duckln-vm-fi")

        text = chat._status_text()
        self.assertIn("local-duckln-vm-fi", text)

    def test_terminal_output_infers_vm_connection_from_ubuntu_prompt(self) -> None:
        self.assertEqual("vm", infer_terminal_connection_from_output("ubuntu@duckln-vm-fi:~$ "))
        self.assertEqual("vm", infer_terminal_connection_from_output("Welcome to Ubuntu 24.04.4 LTS"))
        self.assertIsNone(infer_terminal_connection_from_output("(base) shreyas@Shreyass-MacBook-Air % "))

    def test_wrap_chat_message_reflows_bullets_to_fit_chat_width(self) -> None:
        message = "- Useable RAM: About 5.0 GiB after normal system overhead and swap pressure is likely."
        wrapped = _wrap_chat_message(message, prefix="● ", width=30)

        self.assertIn("\n", wrapped)
        self.assertIn("● - ", wrapped)
        self.assertIn("\n  ", wrapped)

    def test_wrap_chat_message_preserves_blank_lines(self) -> None:
        wrapped = _wrap_chat_message("Line one\n\nLine two", prefix="> ", width=20)

        self.assertIn("> Line one", wrapped)
        self.assertIn("\n  \n", wrapped)
        self.assertIn("  Line two", wrapped)

    def test_wrap_chat_message_keeps_urls_whole_for_links(self) -> None:
        url = "https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=abc&scope=openid"

        wrapped = _wrap_chat_message(f"Open {url}", prefix="● ", width=28)

        self.assertIn(url, wrapped)
        self.assertNotIn("client_\n", wrapped)

    def test_extract_terminal_urls_reads_raw_auth_link(self) -> None:
        url = "https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=abc&scope=openid"

        urls = _extract_terminal_urls(f"Your browser has been opened to visit:\n\n  {url}\n")

        self.assertEqual((url,), urls)

    def test_extract_terminal_urls_waits_for_chunk_boundary(self) -> None:
        urls = _extract_terminal_urls("Your browser has been opened to visit:\nhttps://accounts.google.com/o/oauth2/auth?client")

        self.assertEqual((), urls)

    def test_overlay_choice_labels_round_trip_to_raw_values(self) -> None:
        choices = ("Cancel and return", "Paste a public GitHub repo URL")
        display_label = _overlay_choice_label(choices[1])

        self.assertEqual("• Paste a public GitHub repo URL", display_label)
        self.assertEqual(choices[1], _resolve_overlay_choice(display_label, choices=choices))

    def test_choice_activity_text_uses_approval_wording_for_yes_no_prompts(self) -> None:
        text = _choice_activity_text(
            "Approve Duckln runtime install in this VM now?",
            ("Yes - install Duckln runtime in VM", "No - keep VM ready only"),
        )
        self.assertIn("Awaiting approval:", text)
        self.assertIn("Approve Duckln runtime install", text)

    def test_attachment_button_label_reflects_attachment_count(self) -> None:
        self.assertEqual("📎", _attachment_button_label(()))
        self.assertEqual("📎1", _attachment_button_label(("/tmp/a.txt",)))
        self.assertEqual("📎+", _attachment_button_label(tuple(f"/tmp/{index}.txt" for index in range(12))))

    def test_normalize_attachment_path_accepts_existing_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = f"{temp_dir}/sample.txt"
            with open(candidate, "w", encoding="utf-8") as handle:
                handle.write("hello")
            self.assertTrue(_normalize_attachment_path(candidate).endswith("sample.txt"))
            self.assertIsNone(_normalize_attachment_path(f"{temp_dir}/missing.txt"))

    def test_slash_command_suggestions_include_health_alias(self) -> None:
        self.assertIn("/healthcheck", _slash_command_suggestions("/hea"))
        self.assertEqual((), _slash_command_suggestions("health"))

    def test_slash_command_labels_round_trip_cleanly(self) -> None:
        label = _slash_command_label("/healthcheck")

        self.assertIn("/healthcheck", label)
        self.assertEqual("/healthcheck", _extract_slash_command_from_label(label))

    def test_transient_activity_update_collapses_ollama_progress_lines(self) -> None:
        self.assertEqual(
            ("Ollama pull — pulling manifest", True),
            _transient_activity_update("pulling manifest"),
        )
        self.assertEqual(
            (f"Ollama pull 47% {_render_progress_bar(47)} 759 MB/1.6 GB", True),
            _transient_activity_update("pulling 7462734796d6... 47%      759 MB/1.6 GB"),
        )
        self.assertEqual(("Ollama pull complete", False), _transient_activity_update("success"))

    def test_parse_trace_message_extracts_summary_and_details(self) -> None:
        title, summary, details = _parse_trace_message(
            "Duckln trace: repo runtime review.\n1. Tool: Process and runtime sessions\n2. Action: Run whisper"
        )

        self.assertEqual("repo runtime review", title)
        self.assertEqual("1. Tool: Process and runtime sessions", summary)
        self.assertIn("2. Action: Run whisper", details)

    def test_repair_trace_title_detection_covers_runtime_repair_flow(self) -> None:
        self.assertTrue(_is_repair_trace_title("runtime repair planning"))
        self.assertTrue(_is_repair_trace_title("runtime repair evidence"))
        self.assertTrue(_is_repair_trace_title("post-repair rerun"))
        self.assertFalse(_is_repair_trace_title("web documentation lookup"))

    def test_repair_adjacent_trace_title_detection_covers_operational_repair_updates(self) -> None:
        self.assertTrue(_is_repair_adjacent_trace_title("repo runtime review"))
        self.assertTrue(_is_repair_adjacent_trace_title("shell command execution"))
        self.assertTrue(_is_repair_adjacent_trace_title("web documentation lookup"))
        self.assertFalse(_is_repair_adjacent_trace_title("provider connectivity review"))

    def test_repair_session_message_payload_detects_live_repair_progress(self) -> None:
        payload = _repair_session_message_payload(
            "Supervisor agent installed the missing prerequisite ffmpeg and will re-check whisper now."
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertIn("Supervisor agent installed the missing prerequisite ffmpeg", payload[0])
        self.assertIn("re-check whisper", payload[1])

    def test_trace_keyboard_bindings_are_registered(self) -> None:
        if SplitPaneChatInterface is None:
            self.skipTest("optional split pane dependencies unavailable")

        from duckln.textual_ui import DucklnSplitPaneApp

        bindings = {binding[0]: binding[1] for binding in DucklnSplitPaneApp.BINDINGS}

        self.assertEqual("toggle_repair_details", bindings["ctrl+r"])
        self.assertEqual("dismiss_repair_panel", bindings["ctrl+shift+r"])
        self.assertEqual("toggle_trace_details", bindings["ctrl+e"])
        self.assertEqual("dismiss_trace_panel", bindings["ctrl+shift+e"])
        self.assertEqual("copy_terminal_text", bindings["ctrl+shift+c"])
        self.assertEqual("paste_to_terminal", bindings["ctrl+shift+v"])

    @patch("duckln.textual_ui.platform.system", return_value="Darwin")
    @patch("duckln.textual_ui.shutil.which", return_value="/usr/bin/pbpaste")
    @patch("duckln.textual_ui.subprocess.run")
    def test_read_os_clipboard_text_uses_platform_clipboard(self, run_mock, _which_mock, _system_mock) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "hello"

        self.assertEqual("hello", _read_os_clipboard_text())
        run_mock.assert_called_once()

    def test_status_payload_from_box_extracts_title_and_message(self) -> None:
        payload = "\033[38;2;97;97;97m┌ Supervisor agent ┐\033[0m\n│ Starting whisper [##--] 1/2 • 0s │\n└────────┘"

        title, body = _status_payload_from_box(payload)

        self.assertEqual("Supervisor agent", title)
        self.assertIn("Starting whisper", body)
        self.assertNotIn("\033[", _strip_ansi(payload))

    def test_heading_heuristic_prefers_short_section_titles(self) -> None:
        self.assertTrue(_looks_like_heading("Tracked repo lifecycle"))
        self.assertFalse(_looks_like_heading("Duckln is ready to run whisper when you want to start it."))

    def test_user_lines_do_not_highlight_slash_commands_in_body(self) -> None:
        if SplitPaneChatInterface is None:
            self.skipTest("optional split pane dependencies unavailable")

        lines = _render_wrapped_role_lines(
            "/repos active",
            prefix="> ",
            prefix_color="#ffffff",
            body_color="#eeeeee",
            width=40,
            highlight_inline=False,
        )

        self.assertEqual("> /repos active", lines[0].plain)
        self.assertEqual(2, len(lines[0].spans))

    def test_short_user_and_assistant_lines_do_not_promote_body_text_to_brand_colour(self) -> None:
        if SplitPaneChatInterface is None:
            self.skipTest("optional split pane dependencies unavailable")

        assistant = _render_wrapped_role_lines(
            "Hi there",
            prefix="● ",
            prefix_color="#F5A623",
            body_color="#FFFFFF",
            width=40,
        )
        user = _render_wrapped_role_lines(
            "hi there",
            prefix="> ",
            prefix_color="#FFFFFF",
            body_color="#FFFFFF",
            width=40,
            highlight_inline=False,
        )

        self.assertEqual("bold #FFFFFF", assistant[0].spans[1].style)
        self.assertEqual("bold #FFFFFF", user[0].spans[1].style)

    def test_split_pane_open_web_preview_prefers_managed_browser_window(self) -> None:
        if SplitPaneChatInterface is None:
            self.skipTest("optional split pane dependencies unavailable")

        chat = SplitPaneChatInterface(session_header="HEADER", user_name="Shreyas")

        with patch("duckln.textual_ui.ManagedBrowserWindow.open", return_value=True) as open_mock:
            opened = chat.open_web_preview(url="http://localhost:3000", title_hint="open-webui")

        self.assertTrue(opened)
        open_mock.assert_called_once_with(url="http://localhost:3000", title_hint="open-webui")

    def test_assistant_lines_highlight_numbered_evidence_tokens(self) -> None:
        if SplitPaneChatInterface is None:
            self.skipTest("optional split pane dependencies unavailable")

        lines = _render_wrapped_role_lines(
            "Duckln checked docs. Source: [1] https://docs.docker.com/",
            prefix="● ",
            prefix_color="#F5A623",
            body_color="#FFFFFF",
            width=80,
            highlight_inline=True,
        )

        self.assertIn("[1]", lines[0].plain)
        self.assertTrue(any("link https://docs.docker.com/" in span.style for span in lines[0].spans if span.style))


@unittest.skipIf(SplitPaneChatInterface is None, "optional split pane dependencies unavailable")
class TextualUiInteractionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from duckln.textual_ui import DucklnSplitPaneApp, ObservableTerminal
        import queue
        import threading

        self._terminal_start_patch = patch.object(ObservableTerminal, "start", lambda terminal: None)
        self._terminal_start_patch.start()
        self.addCleanup(self._terminal_start_patch.stop)

        self.app = DucklnSplitPaneApp(
            status_text="Duckln • provider OpenAI • model gpt-5.4 • mode HOTL • user Shreyas",
            connection_label=build_connection_context_label(connection_type="local"),
            shell_command="/bin/zsh -l",
            input_queue=queue.Queue(),
            footer_text="/help for commands",
            ready_event=threading.Event(),
        )
        self._pilot_ctx = self.app.run_test()
        self.pilot = await self._pilot_ctx.__aenter__()

    async def asyncTearDown(self) -> None:
        await self._pilot_ctx.__aexit__(None, None, None)

    async def test_activity_bar_shows_live_spinner_status(self) -> None:
        self.app.update_activity_text("Fixing openclaw: inspecting compose env vars", spinner=True)
        await self.pilot.pause()

        activity = self.app.query_one("#activity-bar")
        self.assertIn("Fixing openclaw: inspecting compose env vars", str(activity.visual))
        self.assertTrue(self.app._activity_spinner)

        self.app.clear_activity_text()
        await self.pilot.pause()

        self.assertEqual("", str(activity.visual))
        self.assertFalse(self.app._activity_spinner)

    async def test_activity_bar_shows_elapsed_time_and_long_running_notice(self) -> None:
        self.app.update_activity_text("Working... triaging the bash blocker", spinner=True)
        self.app._activity_started_at = time.monotonic() - 125
        self.app._update_activity_bar()
        await self.pilot.pause()

        activity = self.app.query_one("#activity-bar")
        text = str(activity.visual)
        self.assertIn("2m", text)
        self.assertIn("taking longer than expected", text)

    async def test_objective_bar_shows_compact_repo_state_and_clears(self) -> None:
        self.app._repair_panel_inline_mode = False  # Item B: legacy bar-mode test
        self.app.update_objective_text(
            "openclaw • runtime repair • waiting approval • attempt 2/5 • docker • approval needed",
            activity_message="Awaiting approval... openclaw — waiting to repair docker env",
            activity_spinner=False,
        )
        await self.pilot.pause()

        objective = self.app.query_one("#objective-bar")
        activity = self.app.query_one("#activity-bar")
        self.assertIn("Objective:", str(objective.visual))
        self.assertIn("openclaw", str(objective.visual))
        self.assertIn("attempt 2/5", str(objective.visual))
        self.assertIn("Awaiting approval... openclaw", str(activity.visual))

        self.app.update_activity_text("Thinking... checking official docs", spinner=True)
        await self.pilot.pause()
        self.assertIn("Thinking... checking official docs", str(activity.visual))

        self.app.clear_activity_text()
        await self.pilot.pause()
        self.assertIn("Awaiting approval... openclaw", str(activity.visual))

        self.app.clear_objective_text()
        await self.pilot.pause()

        self.assertEqual("", str(objective.visual))
        self.assertEqual("", str(activity.visual))

    async def test_trace_panel_can_expand_and_dismiss_without_chat_spam(self) -> None:
        self.app._repair_panel_inline_mode = False  # Item A1: legacy modal-trace test
        chat_log = self.app.query_one("#chat-scroll")

        self.app.append_message(
            "Duckln trace: repo runtime review.\n"
            "1. Tool: Process and runtime sessions\n"
            "2. Action: Run openclaw",
            role="assistant",
        )
        await self.pilot.pause()

        trace_panel = self.app.query_one("#trace-panel")
        trace_summary = self.app.query_one("#trace-summary")
        trace_body = self.app.query_one("#trace-body")

        self.assertEqual("block", trace_panel.styles.display)
        self.assertEqual("1. Tool: Process and runtime sessions", str(trace_summary.visual))
        self.assertEqual("none", trace_body.styles.display)
        self.assertEqual(0, len(chat_log.lines))

        self.app.action_toggle_trace_details()
        await self.pilot.pause()
        self.assertEqual("block", trace_body.styles.display)

        self.app.action_dismiss_trace_panel()
        await self.pilot.pause()
        self.assertEqual("none", trace_panel.styles.display)

    async def test_repair_panel_deduplicates_same_message_and_stays_out_of_transcript(self) -> None:
        self.app._repair_panel_inline_mode = False  # Item D: legacy modal-mode test
        chat_log = self.app.query_one("#chat-scroll")

        message = "Supervisor agent installed the missing prerequisite ffmpeg and will re-check whisper now."
        self.app.append_message(message, role="assistant")
        self.app.append_message(message, role="assistant")
        await self.pilot.pause()

        repair_panel = self.app.query_one("#repair-panel")
        repair_summary = self.app.query_one("#repair-summary")
        repair_body = self.app.query_one("#repair-body")

        self.assertEqual("block", repair_panel.styles.display)
        self.assertIn("ffmpeg", str(repair_summary.visual))
        self.assertEqual(0, len(chat_log.lines))

        await self.pilot.press("ctrl+r")
        await self.pilot.pause()
        self.assertEqual("block", repair_body.styles.display)
        self.assertEqual(1, self.app._repair_panel_details.count(message))
        activity = self.app.query_one("#activity-bar")
        self.assertIn("re-checking after the repair step", str(activity.visual))

    async def test_repair_panel_rolls_forward_to_latest_step_while_preserving_history(self) -> None:
        self.app._repair_panel_inline_mode = False  # Item D: legacy modal-mode test
        first = "Duckln checked current official docs for this blocker: openclaw compose environment variables."
        second = "Supervisor agent installed the missing prerequisite ffmpeg and will re-check whisper now."

        self.app.append_message(first, role="assistant")
        self.app.append_message(second, role="assistant")
        await self.pilot.pause()

        repair_summary = self.app.query_one("#repair-summary")
        repair_body = self.app.query_one("#repair-body")
        activity = self.app.query_one("#activity-bar")

        self.assertIn("ffmpeg", str(repair_summary.visual))
        self.assertIn("re-checking after the repair step", str(activity.visual))

        await self.pilot.press("ctrl+r")
        await self.pilot.pause()
        body_text = str(repair_body.visual)
        self.assertIn("openclaw compose environment variables", body_text)
        self.assertIn("missing prerequisite ffmpeg", body_text)

    async def test_dependency_approval_overlay_shows_structured_review_state(self) -> None:
        request = DependencyApprovalRequest(
            prompt="Duckln security review for openclaw: install repo dependencies",
            command="python -m pip install -r requirements.txt",
            project_dir="/tmp/openclaw",
            manifest_paths=("/tmp/openclaw/requirements.txt",),
            source_urls=("https://packaging.python.org/en/latest/tutorials/installing-packages/",),
            items=(
                DependencyApprovalItem(
                    item_id="numpy",
                    dependency="numpy",
                    version=None,
                    reason="repo dependency",
                    source_url="https://packaging.python.org/en/latest/tutorials/installing-packages/",
                    installer="pip",
                    risk_note="standard package install",
                    manifest_path="/tmp/openclaw/requirements.txt",
                ),
            ),
        )

        self.app.show_dependency_approval(request)
        await self.pilot.pause()

        overlay = self.app.query_one("#approval-overlay")
        prompt = self.app.query_one("#approval-prompt")
        option_list = self.app.query_one("#approval-list")
        activity = self.app.query_one("#activity-bar")

        self.assertEqual("block", overlay.styles.display)
        self.assertIn("Duckln security review for openclaw", str(prompt.visual))
        self.assertEqual(1, option_list.option_count)
        self.assertIn("Awaiting your approval...", str(activity.visual))

    async def test_dependency_approval_overlay_cancels_when_objective_changes(self) -> None:
        request = DependencyApprovalRequest(
            prompt="Duckln security review for openclaw: install repo dependencies",
            command="python -m pip install -r requirements.txt",
            project_dir="/tmp/openclaw",
            manifest_paths=("/tmp/openclaw/requirements.txt",),
            source_urls=("https://packaging.python.org/en/latest/tutorials/installing-packages/",),
            items=(
                DependencyApprovalItem(
                    item_id="numpy",
                    dependency="numpy",
                    version=None,
                    reason="repo dependency",
                    source_url="https://packaging.python.org/en/latest/tutorials/installing-packages/",
                    installer="pip",
                    risk_note="standard package install",
                    manifest_path="/tmp/openclaw/requirements.txt",
                ),
            ),
        )

        self.app.update_objective_text("openclaw • runtime repair", objective_key="runtime_repair:openclaw")
        self.app.show_dependency_approval(request)
        await self.pilot.pause()

        self.app.update_objective_text("whisper • runtime repair", objective_key="runtime_repair:whisper")
        await self.pilot.pause()

        overlay = self.app.query_one("#approval-overlay")
        decision = self.app._approval_result_queue.get_nowait()

        self.assertEqual("none", overlay.styles.display)
        self.assertFalse(self.app._approval_active)
        self.assertFalse(decision.approved)
        self.assertEqual((), decision.selected_item_ids)

    async def test_choice_overlay_cancels_when_objective_changes(self) -> None:
        self.app.update_objective_text("openclaw • runtime repair", objective_key="runtime_repair:openclaw")
        self.app.show_choice_overlay("Do you want Duckln to run openclaw now?", ("Yes", "No"), objective_key="runtime_repair:openclaw")
        await self.pilot.pause()

        self.app.update_objective_text("whisper • runtime repair", objective_key="runtime_repair:whisper")
        await self.pilot.pause()

        overlay = self.app.query_one("#choice-overlay")
        result = self.app._choice_result_queue.get_nowait()

        self.assertEqual("none", overlay.styles.display)
        self.assertFalse(self.app._choice_active)
        self.assertIsNone(result)

    async def test_text_prompt_overlay_shows_input_required_without_chat_message(self) -> None:
        self.app._repair_panel_inline_mode = False  # Item D: legacy modal-mode test
        chat_log = self.app.query_one("#chat-scroll")

        self.app.show_text_prompt_overlay(
            "Enter the value Duckln should use for <audio-file> while running whisper.",
            help_text="Paste a full local file path. Example: /Users/you/Downloads/audio.mp3.",
            mode="attachment",
        )
        await self.pilot.pause()

        overlay = self.app.query_one("#text-overlay")
        prompt = self.app.query_one("#text-prompt")
        helper = self.app.query_one("#text-help")
        repair_panel = self.app.query_one("#repair-panel")
        activity = self.app.query_one("#activity-bar")

        self.assertEqual("block", overlay.styles.display)
        self.assertIn("<audio-file>", str(prompt.visual))
        self.assertIn("audio.mp3", str(helper.visual))
        self.assertEqual("block", repair_panel.styles.display)
        self.assertIn("Awaiting your input...", str(activity.visual))
        self.assertEqual(0, len(chat_log.lines))

    async def test_text_prompt_overlay_cancels_when_objective_changes(self) -> None:
        self.app.update_objective_text("whisper • runtime repair", objective_key="runtime_repair:whisper")
        self.app.show_text_prompt_overlay(
            "Enter the value Duckln should use for <audio-file> while running whisper.",
            help_text="Paste a full local file path. Example: /Users/you/Downloads/audio.mp3.",
            mode="attachment",
        )
        await self.pilot.pause()

        self.app.update_objective_text("openclaw • runtime repair", objective_key="runtime_repair:openclaw")
        await self.pilot.pause()

        overlay = self.app.query_one("#text-overlay")
        result = self.app._text_prompt_result_queue.get_nowait()

        self.assertEqual("none", overlay.styles.display)
        self.assertFalse(self.app._text_prompt_active)
        self.assertIsNone(result)

    async def test_repair_trace_updates_live_activity_from_current_step_title(self) -> None:
        self.app._repair_panel_inline_mode = False  # Item D: legacy modal-mode test
        self.app.append_message(
            "Duckln trace: runtime repair planning.\n"
            "1. Tool: Controlled shell runner\n"
            "2. Action: Inspect compose env vars",
            role="assistant",
        )
        await self.pilot.pause()

        activity = self.app.query_one("#activity-bar")
        repair_panel = self.app.query_one("#repair-panel")
        self.assertEqual("block", repair_panel.styles.display)
        self.assertIn("Thinking... runtime repair planning", str(activity.visual))

    async def test_repair_adjacent_trace_is_absorbed_into_active_repair_surface(self) -> None:
        self.app._repair_panel_inline_mode = False  # Item D: legacy modal-mode test
        chat_log = self.app.query_one("#chat-scroll")

        self.app.append_message(
            "Duckln checked current official docs for this blocker: openclaw compose environment variables.",
            role="assistant",
        )
        await self.pilot.pause()

        self.app.append_message(
            "Duckln trace: shell command execution.\n"
            "1. Tool: Controlled shell runner\n"
            "2. Action: Run docker compose up",
            role="assistant",
        )
        await self.pilot.pause()

        repair_panel = self.app.query_one("#repair-panel")
        repair_summary = self.app.query_one("#repair-summary")
        repair_body = self.app.query_one("#repair-body")
        trace_panel = self.app.query_one("#trace-panel")
        activity = self.app.query_one("#activity-bar")

        self.assertEqual("block", repair_panel.styles.display)
        self.assertEqual("none", trace_panel.styles.display)
        self.assertIn("1. Tool: Controlled shell runner", str(repair_summary.visual))
        self.assertEqual(0, len(chat_log.lines))

        await self.pilot.press("ctrl+r")
        await self.pilot.pause()
        body_text = str(repair_body.visual)
        self.assertIn("checked current official docs for this blocker", body_text)
        self.assertIn("shell command execution", body_text)
        self.assertIn("Working... shell command execution", str(activity.visual))

    async def test_repair_adjacent_trace_keeps_trace_panel_when_no_active_repair_exists(self) -> None:
        self.app._repair_panel_inline_mode = False  # Item A1: legacy modal-trace test
        self.app.append_message(
            "Duckln trace: shell command execution.\n"
            "1. Tool: Controlled shell runner\n"
            "2. Action: Run docker compose up",
            role="assistant",
        )
        await self.pilot.pause()

        trace_panel = self.app.query_one("#trace-panel")
        repair_panel = self.app.query_one("#repair-panel")

        self.assertEqual("block", trace_panel.styles.display)
        self.assertEqual("none", repair_panel.styles.display)

    async def test_inline_mode_collapses_shell_command_traces_to_a_single_done_line(self) -> None:
        # Two shell.command_runner trace events fire per command (execution + result).
        # In inline mode the user should see ONE step line per command — the running
        # event is suppressed, and the result event renders as a ✓ done step line whose
        # label is the actual command, not the generic "shell command execution" title.
        chat_log = self.app.query_one("#chat-scroll")
        starting_lines = len(chat_log.lines)

        self.app.append_message(
            "Duckln trace: shell command execution.\n"
            "1. Tool: Controlled shell runner (shell.command_runner)\n"
            "2. Action: Run a bounded shell command\n"
            "3. Command: multipass list --format json\n"
            "4. Working directory: /tmp\n"
            "5. Timeout: 5.0s",
            role="assistant",
        )
        await self.pilot.pause()
        after_exec = len(chat_log.lines)
        self.assertEqual(starting_lines, after_exec, "execution event should not write a chat line")

        self.app.append_message(
            "Duckln trace: shell command result.\n"
            "1. Tool: Controlled shell runner (shell.command_runner)\n"
            "2. Action: Completed bounded shell command\n"
            "3. Command: multipass list --format json\n"
            "4. Outcome: exit code 0\n"
            "5. Duration: 0.32s",
            role="assistant",
        )
        await self.pilot.pause()

        rendered = "\n".join(str(line) for line in chat_log.lines[after_exec:])
        self.assertIn("multipass list --format json", rendered)
        self.assertNotIn("shell command execution", rendered)
        self.assertNotIn("shell command result", rendered)
        self.assertIn("✓", rendered)

    async def test_inline_mode_marks_failed_shell_command_with_failed_glyph(self) -> None:
        chat_log = self.app.query_one("#chat-scroll")
        before = len(chat_log.lines)

        self.app.append_message(
            "Duckln trace: shell command execution.\n"
            "1. Tool: Controlled shell runner (shell.command_runner)\n"
            "2. Action: Run a bounded shell command\n"
            "3. Command: multipass info missing-vm --format json",
            role="assistant",
        )
        self.app.append_message(
            "Duckln trace: shell command result.\n"
            "1. Tool: Controlled shell runner (shell.command_runner)\n"
            "2. Command: multipass info missing-vm --format json\n"
            "3. Outcome: exit code 2",
            role="assistant",
        )
        await self.pilot.pause()

        rendered = "\n".join(str(line) for line in chat_log.lines[before:])
        activity = self.app.query_one("#activity-bar")
        self.assertIn("multipass info missing-vm", rendered)
        self.assertIn("✗", rendered)
        self.assertEqual("", str(activity.visual))

    async def test_repair_panel_resets_when_objective_key_changes(self) -> None:
        self.app._repair_panel_inline_mode = False  # Item D: legacy modal-mode test
        self.app.update_objective_text("openclaw • runtime repair", objective_key="runtime_repair:openclaw")
        self.app.append_message(
            "Duckln checked current official docs for this blocker: openclaw compose environment variables.",
            role="assistant",
        )
        await self.pilot.pause()

        self.app.update_objective_text("whisper • runtime repair", objective_key="runtime_repair:whisper")
        self.app.append_message(
            "Supervisor agent installed the missing prerequisite ffmpeg and will re-check whisper now.",
            role="assistant",
        )
        await self.pilot.pause()

        repair_summary = self.app.query_one("#repair-summary")
        await self.pilot.press("ctrl+r")
        await self.pilot.pause()
        repair_body = self.app.query_one("#repair-body")
        body_text = str(repair_body.visual)

        self.assertIn("ffmpeg", str(repair_summary.visual))
        self.assertIn("ffmpeg", body_text)
        self.assertNotIn("openclaw compose environment variables", body_text)

    async def test_trace_panel_resets_when_objective_key_changes(self) -> None:
        self.app._repair_panel_inline_mode = False  # Item A1: legacy modal-trace test
        self.app.update_objective_text("openclaw • runtime repair", objective_key="runtime_repair:openclaw")
        self.app.append_message(
            "Duckln trace: web documentation lookup.\n"
            "1. Tool: Web and trusted public source lookup\n"
            "2. Action: Inspect compose environment variables",
            role="assistant",
        )
        await self.pilot.pause()

        trace_panel = self.app.query_one("#trace-panel")
        self.assertEqual("block", trace_panel.styles.display)

        self.app.update_objective_text("whisper • runtime repair", objective_key="runtime_repair:whisper")
        await self.pilot.pause()

        trace_body = self.app.query_one("#trace-body")
        self.assertEqual("none", trace_panel.styles.display)
        self.assertEqual("", str(trace_body.visual))

    async def test_terminal_incident_immediately_sets_triage_activity(self) -> None:
        incident = {
            "category": "missing_command",
            "summary": "Duckln classified this blocker as missing command. Likely package/module: ffmpeg.",
            "package_hint": "ffmpeg",
            "tool_hint": "",
        }

        self.app._handle_terminal_incident(incident)
        await self.pilot.pause()

        activity = self.app.query_one("#activity-bar")
        queued = self.app.consume_terminal_incidents()

        self.assertIn("triaging the ffmpeg blocker", str(activity.visual))
        self.assertEqual(("missing_command",), tuple(item["category"] for item in queued))


class ObjectiveChatSignificanceTest(unittest.TestCase):
    def test_active_phases_share_the_same_significance_key(self) -> None:
        key_repair = _objective_chat_significance("obj-1", "openclaw • repo deploy • repairing vm transport • can continue • attempt 1/5 • ubuntu vm")
        key_clone = _objective_chat_significance("obj-1", "openclaw • repo deploy • cloning repository • can continue • attempt 1/5 • ubuntu vm")
        self.assertEqual(key_repair, key_clone)

    def test_decision_status_produces_distinct_key_from_active(self) -> None:
        key_active = _objective_chat_significance("obj-1", "openclaw • repo deploy • can continue • attempt 1/5 • ubuntu vm")
        key_decision = _objective_chat_significance("obj-1", "openclaw • repo deploy • waiting on you • attempt 1/5 • ubuntu vm")
        self.assertNotEqual(key_active, key_decision)

    def test_failed_status_produces_distinct_key_from_active(self) -> None:
        key_active = _objective_chat_significance("obj-1", "openclaw • repo deploy • can continue • ubuntu vm")
        key_failed = _objective_chat_significance("obj-1", "openclaw • repo deploy • failed • decide next step • ubuntu vm")
        self.assertNotEqual(key_active, key_failed)

    def test_different_objective_keys_always_differ(self) -> None:
        key_a = _objective_chat_significance("obj-1", "openclaw • repo deploy • can continue")
        key_b = _objective_chat_significance("obj-2", "openclaw • repo deploy • can continue")
        self.assertNotEqual(key_a, key_b)

    def test_decide_next_step_marker_maps_to_decision_status(self) -> None:
        sig = _objective_chat_significance("obj-1", "openclaw • repo deploy • failed • decide next step • ubuntu vm")
        self.assertIn("decision", sig)


if __name__ == "__main__":
    unittest.main()
