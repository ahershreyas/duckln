"""Plan 151 — don't run `multipass exec` inside the VM; cd to the VM path, not the Mac path.
F1 the host `multipass exec <vm> -- bash -lc '<body>'` wrapper is unwrapped to `<body>` (the
   pane is already in the VM). F2 the open-in-pane cd target is `$HOME/.duckln/projects/<name>`.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from duckln.shell import _unwrap_vm_pane_command
from duckln.main import _build_repo_attach_command


class UnwrapMultipassInVmPane(unittest.TestCase):
    def test_unwraps_to_inner_body(self):
        wrapped = "multipass exec duckln-vm -- bash -lc 'cd \"$HOME/x\" && exec /bin/bash -l'"
        self.assertEqual(_unwrap_vm_pane_command(wrapped), 'cd "$HOME/x" && exec /bin/bash -l')

    def test_non_multipass_unchanged(self):
        self.assertEqual(_unwrap_vm_pane_command("cd ~/x && ls"), "cd ~/x && ls")


class OpenInPaneUsesVmPath(unittest.TestCase):
    def _vm_row(self):
        return SimpleNamespace(
            execution_target="vm", vm_name="duckln-vm", repo_path="/Users/shreyas/.duckln/projects/JustHireMe",
            metadata={"install_location": "/Users/shreyas/.duckln/projects/JustHireMe"},
        )

    def test_cd_target_is_vm_home_not_mac_path(self):
        cmd, _cwd, label = _build_repo_attach_command(
            config_dir=Path("/tmp"), row=self._vm_row(), workflow=None, repo_key="k", repo_name="JustHireMe",
        )
        self.assertIn("$HOME/.duckln/projects/JustHireMe", cmd)
        self.assertNotIn("/Users/shreyas", cmd)          # never the Mac path on the VM
        self.assertIn("multipass exec duckln-vm", cmd)    # still host-wrapped (unwrapped at the pane)
        # when unwrapped for an in-VM pane, the inner cmd expands $HOME (no quoted ~)
        inner = _unwrap_vm_pane_command(cmd)
        self.assertIn('"$HOME/.duckln/projects/JustHireMe"', inner)


if __name__ == "__main__":
    unittest.main()
