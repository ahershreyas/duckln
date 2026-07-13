"""Plan 150 (cont.) — @-mention delete controls + the 7-day deletion audit.
- store.record_deletion / list_recent_deletions (7-day window).
- delete_controls: mention_suggestions, resolve_delete_target, perform_delete (confirm + record + tell-user).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from state.access import initialize_state_store
from duckln import delete_controls as dc


class DeletionAudit(unittest.TestCase):
    def test_record_and_list_within_7_days(self):
        with tempfile.TemporaryDirectory() as d:
            st = initialize_state_store(Path(d))
            st.record_deletion(kind="vm", name="duckln-vm", detail="multipass delete")
            rows = st.list_recent_deletions(within_days=7)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "vm")
            self.assertEqual(rows[0]["name"], "duckln-vm")

    def test_record_prunes_beyond_retention(self):
        with tempfile.TemporaryDirectory() as d:
            st = initialize_state_store(Path(d))
            # backdate a row 10 days, then record (which prunes < 7 days old)
            with st._connect() as c:
                c.execute("INSERT INTO deletion_audit (kind,name,detail,deleted_at) "
                          "VALUES ('vm','old-vm','x', datetime('now','-10 days'))")
            st.record_deletion(kind="vm", name="new-vm")
            names = {r["name"] for r in st.list_recent_deletions(within_days=7)}
            self.assertIn("new-vm", names)
            self.assertNotIn("old-vm", names)        # pruned (older than 7 days)


class MentionSuggestions(unittest.TestCase):
    def _store_with_custom(self, d):
        st = initialize_state_store(Path(d))
        st.upsert_recent_custom_repo(repo_url="https://github.com/x/JustHireMe", repo_name="JustHireMe",
                                     stars=1, description="x", category="web", framework="TS", last_updated="2026-06-15")
        return st

    def test_lists_type_labeled_targets(self):
        with tempfile.TemporaryDirectory() as d:
            st = self._store_with_custom(d)
            sugg = dc.mention_suggestions(Path(d), vm_lister=lambda: ("duckln-vm",), store=st)
            labels = [t.label for t in sugg]
            self.assertIn("@duckln-vm (VM)", labels)
            self.assertTrue(any("(custom repo)" in l for l in labels))

    def test_query_filters(self):
        with tempfile.TemporaryDirectory() as d:
            st = self._store_with_custom(d)
            sugg = dc.mention_suggestions(Path(d), query="@duck", vm_lister=lambda: ("duckln-vm", "other"), store=st)
            self.assertEqual([t.name for t in sugg], ["duckln-vm"])


class ResolveTarget(unittest.TestCase):
    def _store(self, d):
        st = initialize_state_store(Path(d))
        st.upsert_recent_custom_repo(repo_url="https://github.com/x/Solo", repo_name="Solo",
                                     stars=1, description="x", category="web", framework="TS", last_updated="2026-06-15")
        return st

    def test_at_mention_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            t = dc.resolve_delete_target("delete @duckln-vm", Path(d), vm_lister=lambda: ("duckln-vm",), store=st)
            self.assertIsNotNone(t)
            self.assertEqual((t.kind, t.name), ("vm", "duckln-vm"))

    def test_phrase_single_kind_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            t = dc.resolve_delete_target("delete the vm", Path(d), vm_lister=lambda: ("only-vm",), store=st)
            self.assertEqual(t.name, "only-vm")

    def test_non_delete_message_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(dc.resolve_delete_target("what does the vm do?", Path(d), vm_lister=lambda: ("v",)))


class PerformDelete(unittest.TestCase):
    def test_cancel_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            st = initialize_state_store(Path(d))
            msgs = []
            t = dc.DeletableTarget("vm", "duckln-vm", "@duckln-vm (VM)")
            ok = dc.perform_delete(t, config_dir=Path(d), run_cmd=lambda c: 0,
                                   confirm=lambda _m: False, display=msgs.append, store=st)
            self.assertFalse(ok)
            self.assertTrue(any("NOT deleted" in m for m in msgs))
            self.assertEqual(st.list_recent_deletions(), ())   # nothing recorded

    def test_vm_delete_records_and_tells_7_days(self):
        with tempfile.TemporaryDirectory() as d:
            st = initialize_state_store(Path(d))
            msgs, ran = [], []
            t = dc.DeletableTarget("vm", "duckln-vm", "@duckln-vm (VM)")
            ok = dc.perform_delete(t, config_dir=Path(d), run_cmd=lambda c: ran.append(c) or 0,
                                   confirm=lambda _m: True, display=msgs.append, store=st, execution_target="vm")
            self.assertTrue(ok)
            self.assertIn("multipass delete duckln-vm --purge", ran[0])
            self.assertTrue(any("kept for 7 days" in m for m in msgs))
            self.assertEqual(st.list_recent_deletions()[0]["name"], "duckln-vm")

    def test_custom_repo_forgotten_no_files(self):
        with tempfile.TemporaryDirectory() as d:
            st = initialize_state_store(Path(d))
            st.upsert_recent_custom_repo(repo_url="https://github.com/x/Solo", repo_name="Solo",
                                         stars=1, description="x", category="web", framework="TS", last_updated="2026-06-15")
            msgs = []
            t = dc.DeletableTarget("custom_repo", "https://github.com/x/Solo", "@Solo (custom repo)")
            ok = dc.perform_delete(t, config_dir=Path(d), run_cmd=None,
                                   confirm=lambda _m: True, display=msgs.append, store=st)
            self.assertTrue(ok)
            self.assertTrue(any("installed files are untouched" in m for m in msgs))
            self.assertTrue(any("kept for 7 days" in m for m in msgs))
            self.assertEqual(st.list_recent_custom_repos(), ())   # forgotten from the list


if __name__ == "__main__":
    unittest.main()
