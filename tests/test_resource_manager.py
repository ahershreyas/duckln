from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duckln import resource_manager as rm
from state.access import read_resource_log, write_resource_event


def _snap(**kw):
    base = dict(ram_mb=2048, swap_on=False, disk_total_mb=13000, disk_free_mb=609, disk_used_pct=96, cpu_cores=2)
    base.update(kw)
    return rm.ResourceSnapshot(**base)


class TestCrunchDetection(unittest.TestCase):
    def test_crunch_from_disk_error(self):
        self.assertEqual(rm.crunch_from_error("error: No space left on device").resource, "disk")
        self.assertEqual(rm.crunch_from_error("write error: no space").resource, "disk")

    def test_crunch_from_oom(self):
        self.assertEqual(rm.crunch_from_error("(signal: 9, SIGKILL: kill)").resource, "ram")
        self.assertEqual(rm.crunch_from_error("cc1plus: out of memory").resource, "ram")

    def test_no_crunch_for_other_errors(self):
        self.assertIsNone(rm.crunch_from_error("ModuleNotFoundError: no module named requests"))
        self.assertIsNone(rm.crunch_from_error("operation timed out"))  # timeout != OOM

    def test_detect_crunch_high_disk(self):
        crunches = rm.detect_crunch(_snap(), rm.ResourceNeed())
        self.assertTrue(any(c.resource == "disk" for c in crunches))


class TestDerivedNotHardcoded(unittest.TestCase):
    def test_disk_recommendation_moves_with_current(self):
        c = rm.crunch_from_error("No space left on device")
        host = _snap(ram_mb=16384, disk_total_mb=500000, disk_free_mb=200000)
        rec_small = rm.recommend(c, snapshot=_snap(disk_total_mb=13000), need=rm.estimate_requirement(snapshot=_snap(disk_total_mb=13000), error_text="no space", repo_size_mb=2000), host=host, execution_target="vm", vm_name="v")
        rec_big = rm.recommend(c, snapshot=_snap(disk_total_mb=40000), need=rm.estimate_requirement(snapshot=_snap(disk_total_mb=40000), error_text="no space", repo_size_mb=2000), host=host, execution_target="vm", vm_name="v")
        # recommendation tracks current disk size — it's NOT a fixed constant.
        self.assertNotEqual(rec_small.recommended_mb, rec_big.recommended_mb)
        self.assertGreater(rec_small.recommended_mb, 13000)

    def test_ram_oom_scales_relative_to_current(self):
        c = rm.crunch_from_error("(signal: 9, SIGKILL: kill)")
        host = _snap(ram_mb=32768)
        need = rm.estimate_requirement(snapshot=_snap(ram_mb=2048), error_text="(signal: 9, SIGKILL: kill)")
        rec = rm.recommend(c, snapshot=_snap(ram_mb=2048), need=need, host=host, execution_target="vm", vm_name="v")
        self.assertEqual(rec.recommended_mb, 4096)  # 2× current, not a magic number
        self.assertIn("derived", rec.explanation.lower())


class TestHostBound(unittest.TestCase):
    def test_recommendation_capped_by_host(self):
        c = rm.crunch_from_error("(signal: 9, SIGKILL: kill)")
        small_host = _snap(ram_mb=4096)  # host can't give a VM 4GB+ (leaves 2GB headroom)
        need = rm.estimate_requirement(snapshot=_snap(ram_mb=2048), error_text="signal: 9")
        rec = rm.recommend(c, snapshot=_snap(ram_mb=2048), need=need, host=small_host, execution_target="vm", vm_name="v")
        self.assertTrue(rec.host_capped)
        self.assertLessEqual(rec.recommended_mb, 4096 - 2048)  # bounded by host - headroom
        # When the host can't help, it's honest (not feasible) rather than recommending the impossible.
        self.assertFalse(rec.feasible)

    def test_cloud_has_cost_note(self):
        c = rm.crunch_from_error("(signal: 9, SIGKILL: kill)")
        need = rm.estimate_requirement(snapshot=_snap(ram_mb=2048), error_text="signal: 9")
        rec = rm.recommend(c, snapshot=_snap(ram_mb=2048), need=need, host=None, execution_target="aws", vm_name="i")
        self.assertIn("cost", rec.cost_note.lower())


class TestProbeParsing(unittest.TestCase):
    def test_parse_block(self):
        out = (
            "Filesystem 1048576-blocks Used Available Capacity Mounted\n"
            "/dev/sda1 13000 12391 609 96% /\n"
            "DUCKLN_CPU:2\n"
            "MemTotal:        2048000 kB\n"
            "DUCKLN_SWAP:0\n"
        )
        snap = rm.parse_resource_block(out)
        self.assertEqual(snap.disk_total_mb, 13000)
        self.assertEqual(snap.disk_free_mb, 609)
        self.assertEqual(snap.disk_used_pct, 96)
        self.assertEqual(snap.cpu_cores, 2)
        self.assertEqual(snap.ram_mb, 2000)
        self.assertFalse(snap.swap_on)

    def test_probe_script_has_no_single_quotes(self):
        self.assertNotIn("'", rm.RESOURCE_PROBE_SCRIPT)


class TestApplyResizeGuards(unittest.TestCase):
    def test_no_resize_without_approval(self):
        ran = []
        rec = rm.ResourceRecommendation("disk", 13000, 25000, 25000, True, "grow it")
        ok = rm.apply_resize(rec, execution_target="vm", vm_name="v", approve=lambda p: False, prompt_value=None, display=lambda m: ran.append(m))
        self.assertFalse(ok)

    def test_infeasible_not_applied(self):
        rec = rm.ResourceRecommendation("ram", 2048, 2048, 8192, False, "host too small")
        ok = rm.apply_resize(rec, execution_target="vm", vm_name="v", approve=lambda p: True, prompt_value=None, display=lambda m: None)
        self.assertFalse(ok)


class TestResourceLog(unittest.TestCase):
    def test_log_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            write_resource_event(Path(t), target="local-duckln-vm", event="disk crunch: 96% full")
            write_resource_event(Path(t), target="local-duckln-vm", event="resized disk → 25G")
            log = read_resource_log(Path(t), "local-duckln-vm")
            self.assertEqual(len(log), 2)
            self.assertIn("resized", log[-1]["event"])


if __name__ == "__main__":
    unittest.main()
