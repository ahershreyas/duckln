"""Plan 196 — VM/cloud command bugs (F1-F5), natural-language infra inventory (F6-F9, F14),
and the web-recovery/non-technical HITL upgrade (F10-F13)."""

from __future__ import annotations

import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace


class F1NoRepoPlanNoBogusCd(unittest.TestCase):
    def test_no_repo_plan_runs_steps_without_cd(self):
        # A repo_slug=None (infra) plan must run steps with step_cwd=None (no cd into a bogus
        # ~/.duckln/projects/plan-mode). We assert the wrapped command carries no `cd`.
        from duckln import repo_bringup as rb

        captured: list[str] = []

        class FakeRunner:
            execution_target = "local"

            def run(self, command, **kw):
                captured.append(command)
                return SimpleNamespace(stdout="", stderr="", exit_code=0, timed_out=False)

        step = SimpleNamespace(index=1, title="Check Multipass is installed", command="multipass version",
                               safety_class="S0", origin="planner")
        plan = SimpleNamespace(repo_slug=None, objective="Provision an Ubuntu Multipass VM",
                               steps=[step], amendment_count=0, mode="hootlwo")
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            paths = SimpleNamespace(config_dir=cfg)
            current = SimpleNamespace(mode=SimpleNamespace(value="hootlwo", label="HOOTLWO"), plan_mode_enabled=True)
            with mock.patch.object(rb, "read_config_snapshot", return_value={"execution_target": "local", "model": ""}), \
                 mock.patch.object(rb, "build_default_llm_client_or_none", return_value=None) if hasattr(rb, "build_default_llm_client_or_none") else mock.patch.object(rb, "_detect_runtimes_for_understanding", return_value=()):
                try:
                    rb.resume_with_approved_plan(
                        plan=plan, paths=paths, current=current, approve=lambda _m: True,
                        display=lambda _m: None, runner=FakeRunner(),
                    )
                except Exception:
                    pass
        # The multipass version step must have run without a `cd` prefix.
        self.assertTrue(any("multipass version" in c and "cd " not in c for c in captured),
                        f"expected an un-cd'd 'multipass version'; got {captured}")


class F5CdIntoMissingDir(unittest.TestCase):
    def test_valid_cd_missing_dir_gets_mkdir(self):
        from duckln.diagnostics import match_deterministic_fix

        fix = match_deterministic_fix(
            stderr="cd: no such file or directory: /Users/x/.duckln/projects/plan-mode",
            exit_code=1,
            command='cd "/Users/x/.duckln/projects/plan-mode" && multipass version',
        )
        self.assertIsNotNone(fix)
        self.assertTrue(fix.fix_command.startswith("mkdir -p"))
        self.assertIn("multipass version", fix.fix_command)
        self.assertEqual(fix.safety_class, "S2")

    def test_slash_cd_typo_still_self_corrects(self):
        from duckln.diagnostics import match_deterministic_fix

        fix = match_deterministic_fix(stderr="/cd: No such file or directory", exit_code=127,
                                      command='/cd "/x" && multipass version')
        self.assertIsNotNone(fix)
        self.assertFalse(fix.fix_command.startswith("mkdir -p"))
        self.assertTrue(fix.fix_command.startswith("cd "))

    def test_unrelated_error_untouched(self):
        from duckln.diagnostics import match_deterministic_fix

        self.assertIsNone(match_deterministic_fix(stderr="some other error", exit_code=1, command="npm run build"))


class F11AllowList(unittest.TestCase):
    def test_docs_vendor_domains_allowed_retail_excluded(self):
        from duckln.authoritative_sources import is_authoritative_url

        for url in (
            "https://canonical.com/multipass/docs/set-up",
            "https://ubuntu.com/server/docs/virtualisation-multipass",
            "https://docs.aws.amazon.com/cli/latest/",
            "https://docs.cloud.google.com/sdk/docs",
            "https://github.com/canonical/multipass",
            "https://docs.docker.com/engine/",
        ):
            self.assertTrue(is_authoritative_url(url), url)
        for url in ("https://www.amazon.com/dp/B0", "https://www.google.com/search?q=x", "https://www.docker.com/pricing"):
            self.assertFalse(is_authoritative_url(url), url)


class F4VmListFailureVsEmpty(unittest.TestCase):
    def test_failure_returns_none_empty_returns_tuple(self):
        from duckln.vm import list_multipass_vms_or_none

        class R:
            def __init__(self, out, code=0, to=False):
                self.out, self.code, self.to = out, code, to

            def run(self, cmd):
                return SimpleNamespace(stdout=self.out, stderr="", exit_code=self.code, timed_out=self.to)

        self.assertIsNone(list_multipass_vms_or_none(runner=R("", code=1)))
        self.assertIsNone(list_multipass_vms_or_none(runner=R("", to=True)))
        self.assertEqual(list_multipass_vms_or_none(runner=R('{"list": []}')), ())
        self.assertEqual(
            list_multipass_vms_or_none(runner=R('{"list": [{"name": "duckln-vm"}]}')), ("duckln-vm",)
        )


class F7DockerInventory(unittest.TestCase):
    def _runner(self, out, code=0):
        class R:
            def run(self, cmd, timeout_seconds=15.0):
                return SimpleNamespace(stdout=out, stderr="", exit_code=code, timed_out=False)
        return R()

    def test_images_parse_and_failure(self):
        from duckln.docker_inventory import list_docker_images

        inv = list_docker_images(runner=self._runner('{"Repository":"python","Tag":"3.12","ID":"a","Size":"1GB"}'))
        self.assertTrue(inv.ok)
        self.assertEqual(len(inv.images), 1)
        self.assertEqual(inv.images[0].name, "python:3.12")
        self.assertFalse(list_docker_images(runner=self._runner("", code=127)).ok)

    def test_running_only(self):
        from duckln.docker_inventory import list_docker_containers

        inv = list_docker_containers(running_only=True, runner=self._runner('{"ID":"c1","Names":"app","Image":"i","Status":"Up 2h"}'))
        self.assertTrue(inv.ok and inv.containers[0].running)


class F6InventoryAnswerers(unittest.TestCase):
    def test_vm_answer_joins_repos(self):
        from duckln import main as M

        row = SimpleNamespace(execution_target="vm", vm_name="duckln-vm",
                              repo_url="https://github.com/x/JustHireMe", metadata={"repo_name": "JustHireMe"})
        store = SimpleNamespace(list_repo_states=lambda: (row,))
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(M, "list_multipass_vms_or_none", return_value=("duckln-vm", "test-vm")), \
                 mock.patch.object(M, "initialize_state_store", return_value=store):
                out = M._inventory_vm_answer(Path(td))
        self.assertIn("duckln-vm", out)
        self.assertIn("JustHireMe", out)
        self.assertIn("test-vm", out)

    def test_docker_answer_unavailable(self):
        from duckln import main as M

        # Plan 197 F5: when docker isn't usable, the answer is actionable (install or start),
        # never a crash — regardless of whether docker is installed on the test host.
        with tempfile.TemporaryDirectory() as td:
            out = M._inventory_docker_answer(Path(td))
        self.assertTrue("Docker isn't installed" in out or "isn't running" in out, out)

    def test_routing_precision(self):
        from duckln import main as M

        self.assertTrue(M._INV_VM_RE.search("list all vms and their respective repos"))
        self.assertTrue(M._INV_DOCKER_RE.search("how many docker images are in use"))
        self.assertTrue(M._INV_REPO_RE.search("how many repos have we set up"))
        # a catalog question / a normal task must NOT match the session-repo inventory pattern
        self.assertFalse(M._INV_REPO_RE.search("how many repos do you have access to"))
        self.assertFalse(M._INV_VM_RE.search("set up justhireme"))


class F9ProjectAppPhrasing(unittest.TestCase):
    def _match(self, msg):
        from duckln import main as M

        m = msg.lower()
        return bool(M._PREV_REPO_RE.search(m) and M._PREV_REPO_RECENCY_RE.search(m) and M._PREV_REPO_SUBJECT_RE.search(m))

    def test_project_app_recognized(self):
        self.assertTrue(self._match("what was our last project"))
        self.assertTrue(self._match("our last app we worked on"))
        self.assertTrue(self._match("what was our last repo"))

    def test_no_false_positive(self):
        self.assertFalse(self._match("how do I build an app"))
        self.assertFalse(self._match("recommend a repo"))


class F14InventoryCascade(unittest.TestCase):
    def test_loose_prefilter(self):
        from duckln import main as M

        self.assertFalse(M._INV_LOOSE_RE.search("hi there"))
        self.assertFalse(M._INV_LOOSE_RE.search("how are you"))
        self.assertTrue(M._INV_LOOSE_RE.search("which machines do I have and what's on them"))
        self.assertTrue(M._INV_LOOSE_RE.search("are any containers up"))
        self.assertTrue(M._INV_LOOSE_RE.search("what were we building yesterday"))

    def test_classifier_parses(self):
        from duckln.conversation_routes.llm_intent import classify_inventory_intent

        self.assertEqual(classify_inventory_intent("x", llm_client=None), ("none", 0.0))
        client = lambda system_prompt="", user_message="": '{"intent":"inventory_docker","confidence":0.9}'
        self.assertEqual(classify_inventory_intent("any containers up", llm_client=client), ("inventory_docker", 0.9))


class F10HtmlToMarkdown(unittest.TestCase):
    def test_fenced_code_headings_lists(self):
        from duckln.web_runtime import _extract_html_excerpt

        html = (
            "<html><body><script>var x=1;</script>"
            "<h2>Install</h2><p>Run:</p>"
            "<pre><code class=\"language-bash\">docker run -d nginx</code></pre>"
            "<ul><li>One</li><li>Two</li></ul><ol><li>A</li><li>B</li></ol></body></html>"
        )
        md = _extract_html_excerpt(html)
        self.assertIn("```bash", md)
        self.assertIn("docker run -d nginx", md)
        self.assertIn("## Install", md)
        self.assertIn("- One", md)
        self.assertIn("1. A", md)
        self.assertNotIn("var x=1", md)

    def test_malformed_falls_back_no_raise(self):
        from duckln.web_runtime import _extract_html_excerpt

        # Must not raise.
        self.assertIsInstance(_extract_html_excerpt("<pre><code>oops"), str)

    def test_char_cap(self):
        from duckln.web_runtime import _extract_html_excerpt, MAX_REMOTE_SUMMARY_CHARS

        big = "<p>" + ("word " * 20000) + "</p>"
        self.assertLessEqual(len(_extract_html_excerpt(big)), MAX_REMOTE_SUMMARY_CHARS)


class F12F13WebHitl(unittest.TestCase):
    def test_translate_plain_and_bundle(self):
        from duckln.repo_bringup import _translate_error_and_fix_plain, _write_diagnostics_bundle

        floor = _translate_error_and_fix_plain("port 8080 in use", "kill 1", llm_client=None)
        self.assertTrue(floor and "8080" not in floor)  # no raw jargon leaked in the floor
        voiced = _translate_error_and_fix_plain(
            "port 8080 in use", "x",
            llm_client=lambda system_prompt="", user_message="": "Another app is using the same channel.",
        )
        self.assertIn("Another app", voiced)

    def test_bundle_redacts_secrets(self):
        from duckln.repo_bringup import _write_diagnostics_bundle

        with tempfile.TemporaryDirectory() as td:
            p = _write_diagnostics_bundle(
                Path(td), repo_name="JustHireMe",
                error_text="api_key=SECRET123 token=TOKENXYZ port in use", tried="searched web", fix_command="npm i",
            )
            self.assertIsNotNone(p)
            txt = p.read_text()
            self.assertNotIn("SECRET123", txt)
            self.assertNotIn("TOKENXYZ", txt)
            self.assertIn("JustHireMe", txt)


if __name__ == "__main__":
    unittest.main()
