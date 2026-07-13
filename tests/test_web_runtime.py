"""Tests for bounded web reference helpers used in repair paths."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from duckln.web_runtime import (
    build_repo_install_search_query,
    build_runtime_search_query,
    build_runtime_web_reference_note,
    fetch_web_reference_summary,
    gather_runtime_repair_evidence,
    references_for_runtime_issue,
    search_for_repo_install_hint,
    search_for_run_command_hint,
    search_runtime_issue,
    _trusted_source_rank,
)


class WebRuntimeTest(unittest.TestCase):
    def test_references_for_python_module_failure_returns_official_docs(self) -> None:
        references = references_for_runtime_issue(
            command=".venv/bin/python -m whisper --help",
            error_text="ModuleNotFoundError: No module named 'whisper'",
        )

        urls = {reference.url for reference in references}
        self.assertIn("https://packaging.python.org/en/latest/tutorials/installing-packages/", urls)
        self.assertIn("https://github.com/openai/whisper", urls)

    def test_runtime_search_query_redacts_sensitive_local_identifiers(self) -> None:
        query = build_runtime_search_query(
            command="npm install -g openclaw@latest",
            error_text="EACCES on 192.168.1.20 mac aa:bb:cc:dd:ee:ff token=secret123",
            os_hint="Ubuntu Linux VM",
        )

        self.assertNotIn("192.168.1.20", query)
        self.assertNotIn("aa:bb:cc:dd:ee:ff", query)
        self.assertNotIn("secret123", query)
        self.assertIn("[REDACTED_IP]", query)

    def test_build_runtime_web_reference_note_is_compact_and_empty_when_unknown(self) -> None:
        note = build_runtime_web_reference_note(command="docker run hello-world", error_text="docker: command not found")
        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("Docker docs", note)

        self.assertIsNone(build_runtime_web_reference_note(command="custom-tool", error_text="some opaque failure"))

    def test_fetch_web_reference_summary_extracts_title_and_excerpt(self) -> None:
        response = MagicMock()
        response.read.return_value = b"<html><head><title>Docker Docs</title></head><body><main>Install Docker Engine on Ubuntu for a supported setup path.</main></body></html>"
        response.headers.get.return_value = "text/html"
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch("duckln.web_runtime.urlopen", return_value=response):
            summary = fetch_web_reference_summary(references_for_runtime_issue(command="docker", error_text="docker: command not found")[0])

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual("Docker Docs", summary.title)
        self.assertIn("Install Docker Engine", summary.excerpt)

    def test_build_runtime_web_reference_note_prefers_live_summary_when_available(self) -> None:
        with patch("duckln.web_runtime.fetch_web_reference_summary") as fetch_summary:
            fetch_summary.return_value = MagicMock(
                title="Python Packaging",
                excerpt="Install packages into the environment tied to the Python interpreter you plan to run.",
                reference=MagicMock(url="https://packaging.python.org/en/latest/tutorials/installing-packages/"),
            )

            note = build_runtime_web_reference_note(
                command=".venv/bin/python -m whisper --help",
                error_text="ModuleNotFoundError: No module named whisper",
                fetch_live=True,
            )

        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("checked current official docs", note)
        self.assertIn("Python Packaging", note)

    def test_search_runtime_issue_extracts_first_result(self) -> None:
        response = MagicMock()
        response.read.return_value = (
            b'<html><body>'
            b'<a class="result__a" href="https://example.com/fix">Fix guide</a>'
            b'<div class="result__snippet">Use the correct environment before running the module.</div>'
            b"</body></html>"
        )
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch("duckln.web_runtime.urlopen", return_value=response):
            result = search_runtime_issue(command="python -m whisper", error_text="command not found")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("Fix guide", result.title)
        self.assertEqual("https://example.com/fix", result.url)
        self.assertIn("correct environment", result.excerpt)

    def test_build_runtime_web_reference_note_falls_back_to_web_search_when_docs_do_not_resolve(self) -> None:
        with patch("duckln.web_runtime.fetch_web_reference_summary", return_value=None), patch(
            "duckln.web_runtime.search_runtime_issue"
        ) as search_runtime:
            search_runtime.return_value = MagicMock(
                title="Fix guide",
                excerpt="Use the correct environment before running the module.",
                url="https://example.com/fix",
            )

            note = build_runtime_web_reference_note(
                command=".venv/bin/python -m whisper --help",
                error_text="ModuleNotFoundError: No module named whisper",
                fetch_live=True,
            )

        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("broadened the search on the web", note)
        self.assertIn("https://example.com/fix", note)

    def test_build_runtime_search_query_is_bounded_and_readable(self) -> None:
        query = build_runtime_search_query(
            command=".venv/bin/python -m pip install -r requirements.txt",
            error_text="Temporary failure in name resolution",
        )

        self.assertIn("requirements.txt", query)
        self.assertIn("Temporary failure in name resolution", query)
        self.assertIn("github", query)
        self.assertIn("stackoverflow", query)
        self.assertIn("blog", query)
        self.assertIn("forum", query)

    def test_build_runtime_search_query_starts_with_exact_error_phrase(self) -> None:
        query = build_runtime_search_query(
            command="run openclaw",
            error_text="Command 'multipass' not found, but can be installed with:\nsudo snap install multipass",
        )

        self.assertTrue(query.startswith('"Command \'multipass\' not found, but can be installed with:"'))
        self.assertIn("github", query)

    def test_search_ranking_prefers_open_internet_solutions_over_repo_docs(self) -> None:
        stackoverflow_rank = _trusted_source_rank("https://stackoverflow.com/questions/1/fix")
        github_issue_rank = _trusted_source_rank("https://github.com/openclaw/openclaw/issues/12")
        blog_rank = _trusted_source_rank("https://dev.to/user/fix-openclaw")
        repo_doc_rank = _trusted_source_rank("https://github.com/openclaw/openclaw/blob/main/README.md")
        official_docs_rank = _trusted_source_rank("https://docs.openclaw.ai/start/getting-started")

        self.assertLess(stackoverflow_rank, repo_doc_rank)
        self.assertLess(github_issue_rank, repo_doc_rank)
        self.assertLess(blog_rank, repo_doc_rank)
        self.assertLess(repo_doc_rank, official_docs_rank)

    def test_fetch_web_reference_summary_can_emit_trace(self) -> None:
        response = MagicMock()
        response.read.return_value = b"<html><head><title>Docker Docs</title></head><body><main>Install Docker Engine on Ubuntu.</main></body></html>"
        response.headers.get.return_value = "text/html"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        traces: list[str] = []

        with patch("duckln.web_runtime.urlopen", return_value=response):
            summary = fetch_web_reference_summary(
                references_for_runtime_issue(command="docker", error_text="docker: command not found")[0],
                trace=traces.append,
            )

        self.assertIsNotNone(summary)
        self.assertTrue(any("Duckln trace: web documentation lookup." in trace for trace in traces))
        self.assertTrue(any("web.documentation_lookup" in trace for trace in traces))

    def test_search_runtime_issue_can_emit_trace(self) -> None:
        response = MagicMock()
        response.read.return_value = (
            b'<html><body>'
            b'<a class="result__a" href="https://example.com/fix">Fix guide</a>'
            b'<div class="result__snippet">Use the correct environment before running the module.</div>'
            b"</body></html>"
        )
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        traces: list[str] = []

        with patch("duckln.web_runtime.urlopen", return_value=response):
            result = search_runtime_issue(
                command="python -m whisper",
                error_text="command not found",
                trace=traces.append,
            )

        self.assertIsNotNone(result)
        self.assertTrue(any("Duckln trace: web search lookup." in trace for trace in traces))
        self.assertTrue(any("Search query:" in trace for trace in traces))
        self.assertTrue(any("GitHub issues" in trace for trace in traces))

    def test_search_runtime_issue_tries_exact_error_query_before_broad_query(self) -> None:
        with patch("duckln.web_runtime._search_runtime_issue_candidates") as mock_search:
            mock_search.side_effect = [
                (),
                (
                    MagicMock(
                        title="Multipass command missing",
                        excerpt="Install Multipass on the host, not inside the VM.",
                        url="https://stackoverflow.com/questions/1/multipass-command-not-found",
                    ),
                ),
            ]

            result = search_runtime_issue(
                command="run openclaw",
                error_text="Command 'multipass' not found, but can be installed with:\nsudo snap install multipass",
            )

        self.assertIsNotNone(result)
        first_query = mock_search.call_args_list[0].args[0]
        second_query = mock_search.call_args_list[1].args[0]
        self.assertTrue(first_query.startswith('"Command \'multipass\' not found'))
        self.assertIn("github", second_query)

    def test_gather_runtime_repair_evidence_searches_exact_error_even_with_official_docs(self) -> None:
        with patch("duckln.web_runtime.fetch_web_reference_summary", return_value=None), patch(
            "duckln.web_runtime._search_runtime_issue_candidates"
        ) as mock_search:
            mock_search.return_value = (
                MagicMock(
                    title="Known Docker issue",
                    excerpt="Install or start Docker before running compose.",
                    url="https://stackoverflow.com/questions/1/docker-command-not-found",
                ),
            )

            evidence = gather_runtime_repair_evidence(
                command="docker compose up",
                error_text="docker: command not found",
                fetch_live=True,
            )

        self.assertGreaterEqual(len(mock_search.call_args_list), 1)
        self.assertIsNotNone(evidence.note)
        assert evidence.note is not None
        self.assertIn("exact terminal error", evidence.note)
        self.assertIn("https://stackoverflow.com/questions/1/docker-command-not-found", evidence.source_urls)

    def test_gather_runtime_repair_evidence_prefers_exact_duckduckgo_result_before_docs(self) -> None:
        with patch("duckln.web_runtime.fetch_web_reference_summary") as fetch_docs, patch(
            "duckln.web_runtime._search_runtime_issue_candidates"
        ) as mock_search:
            fetch_docs.return_value = MagicMock(
                title="Docker Docs",
                excerpt="Install Docker Engine.",
                reference=MagicMock(url="https://docs.docker.com/"),
                steps=(),
            )
            mock_search.return_value = (
                MagicMock(
                    title="Docker command not found",
                    excerpt="Install Docker or start the daemon.",
                    url="https://stackoverflow.com/questions/2/docker-command-not-found",
                ),
            )

            evidence = gather_runtime_repair_evidence(
                command="docker compose up",
                error_text="docker: command not found",
                fetch_live=True,
            )

        self.assertIsNotNone(evidence.note)
        assert evidence.note is not None
        self.assertTrue(evidence.note.startswith("Duckln searched the exact terminal error"))
        self.assertIn("stackoverflow.com/questions/2", evidence.source_urls[0])

    def test_search_runtime_issue_prefers_trusted_public_sources(self) -> None:
        response = MagicMock()
        response.read.return_value = (
            b'<html><body>'
            b'<a class="result__a" href="https://example.com/fix">Random fix guide</a>'
            b'<div class="result__snippet">Try a clean environment.</div>'
            b'<a class="result__a" href="https://github.com/acme/project/issues/7">Known issue</a>'
            b'<div class="result__snippet">Maintainers recommend the documented start command.</div>'
            b"</body></html>"
        )
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch("duckln.web_runtime.urlopen", return_value=response):
            result = search_runtime_issue(command="run project", error_text="runtime failed")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("https://github.com/acme/project/issues/7", result.url)

    def test_search_runtime_issue_prefers_exact_error_match_over_unrelated_high_rank_source(self) -> None:
        response = MagicMock()
        response.read.return_value = (
            b'<html><body>'
            b'<a class="result__a" href="https://github.com/acme/project/issues/50">General setup thread</a>'
            b'<div class="result__snippet">How to install the tool generally.</div>'
            b'<a class="result__a" href="https://example.com/multipass-command-not-found-fix">Multipass command not found fix</a>'
            b'<div class="result__snippet">Command \'multipass\' not found, install it on host and retry.</div>'
            b"</body></html>"
        )
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch("duckln.web_runtime.urlopen", return_value=response):
            result = search_runtime_issue(
                command="run openclaw",
                error_text="Command 'multipass' not found, but can be installed with:\nsudo snap install multipass",
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("https://example.com/multipass-command-not-found-fix", result.url)

    def test_build_runtime_search_query_ignores_duckln_status_lines(self) -> None:
        query = build_runtime_search_query(
            command="run openclaw",
            error_text=(
                "Duckln classified this blocker as missing command.\n"
                "Objective: openclaw runtime repair attempt 1/5.\n"
                "Command 'multipass' not found, but can be installed with:\n"
                "sudo snap install multipass"
            ),
        )

        self.assertIn("Command 'multipass' not found", query)
        self.assertNotIn("Duckln classified this blocker", query)

    def test_build_runtime_search_query_ignores_prefixed_classifier_status_lines(self) -> None:
        query = build_runtime_search_query(
            command="run openclaw",
            error_text=(
                "Clone failed: Duckln classified this blocker as missing command.\n"
                "Fatal line: bash: line 1: multipass: command not found"
            ),
        )

        self.assertIn("multipass: command not found", query)
        self.assertNotIn("Duckln classified this blocker", query)


class SearchForRunCommandHintTest(unittest.TestCase):
    def test_returns_evidence_with_note_and_source_url_on_successful_search(self) -> None:
        response = MagicMock()
        response.read.return_value = (
            b'<html><body>'
            b'<a class="result__a" href="https://github.com/openai/whisper">OpenAI Whisper README</a>'
            b'<div class="result__snippet">Run with: python -m whisper audio.mp3</div>'
            b"</body></html>"
        )
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch("duckln.web_runtime.urlopen", return_value=response):
            evidence = search_for_run_command_hint("whisper", repo_url="https://github.com/openai/whisper")

        self.assertIsNotNone(evidence.note)
        assert evidence.note is not None
        self.assertIn("whisper", evidence.note.lower())
        self.assertIn("Source: [1]", evidence.note)
        self.assertTrue(len(evidence.source_urls) > 0)

    def test_returns_empty_evidence_when_search_fails(self) -> None:
        with patch("duckln.web_runtime.urlopen", side_effect=OSError("network error")):
            evidence = search_for_run_command_hint("myrepo")

        self.assertIsNone(evidence.note)
        self.assertEqual((), evidence.source_urls)

    def test_uses_github_focused_query_when_github_url_provided(self) -> None:
        with patch("duckln.web_runtime._search_runtime_issue", return_value=None) as mock_search:
            search_for_run_command_hint("whisper", repo_url="https://github.com/openai/whisper")

        call_args = mock_search.call_args[0][0]
        self.assertIn("github", call_args)
        self.assertIn("README", call_args)
        self.assertIn("issues", call_args)
        self.assertIn("whisper", call_args)

    def test_search_query_is_recorded_in_returned_evidence(self) -> None:
        with patch("duckln.web_runtime._search_runtime_issue", return_value=None):
            evidence = search_for_run_command_hint("myapp")

        self.assertIsNotNone(evidence.search_query)
        assert evidence.search_query is not None
        self.assertIn("myapp", evidence.search_query)

    def test_repo_install_query_includes_repo_url_and_target_os(self) -> None:
        query = build_repo_install_search_query(
            "openclaw",
            repo_url="https://github.com/openclaw/openclaw",
            os_hint="Ubuntu Linux VM",
        )

        self.assertIn("how to install openclaw", query)
        self.assertIn("https://github.com/openclaw/openclaw", query)
        self.assertIn("Ubuntu Linux VM", query)

    def test_repo_install_search_returns_sources_and_commands(self) -> None:
        response = MagicMock()
        response.read.return_value = (
            b'<html><body>'
            b'<a class="result__a" href="https://github.com/openclaw/openclaw">OpenClaw README</a>'
            b'<div class="result__snippet">Install with: npm install -g openclaw. Run openclaw serve.</div>'
            b"</body></html>"
        )
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch("duckln.web_runtime.urlopen", return_value=response):
            evidence = search_for_repo_install_hint(
                "openclaw",
                repo_url="https://github.com/openclaw/openclaw",
                os_hint="Ubuntu Linux VM",
            )

        self.assertIsNotNone(evidence.note)
        self.assertIn("https://github.com/openclaw/openclaw", evidence.source_urls)


if __name__ == "__main__":
    unittest.main()
