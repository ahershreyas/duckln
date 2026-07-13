"""Plan 139 — land the user ON the streamed app, not noVNC's "Connect" page:
the noVNC URL auto-connects + scales so the link opens the desktop directly."""

from __future__ import annotations

import unittest

import duckln.repo_bringup as rb


class NovncAutoConnect(unittest.TestCase):
    def test_url_auto_connects_and_scales(self):
        url = rb._novnc_url(execution_target="local", vm_name=None)
        self.assertIn("vnc.html?", url)
        self.assertIn("autoconnect=true", url)   # skip the Connect landing page
        self.assertIn("resize=scale", url)        # fit the app to the window
        self.assertIn("reconnect=true", url)      # silently re-attach on a drop

    def test_vm_url_uses_vm_host_and_keeps_params(self):
        url = rb._novnc_url(execution_target="vm", vm_name="duckln-vm")
        # host is rewritten away from localhost (to the VM IP) when resolvable…
        # …and the auto-connect query is preserved.
        self.assertIn("autoconnect=true", url)
        self.assertIn(f":{rb._VNC_WEB_PORT}/vnc.html?", url)


if __name__ == "__main__":
    unittest.main()
