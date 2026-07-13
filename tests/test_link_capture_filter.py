from __future__ import annotations

import unittest

from duckln.textual_ui import _is_infra_url


class TestInfraUrlFilter(unittest.TestCase):
    def test_package_mirror_and_doc_hosts_are_infra(self):
        for url in (
            "http://ports.ubuntu.com/ubuntu-ports",
            "https://deb.nodesource.com/setup_20.x",
            "https://ubuntu.com/esm",
            "https://archive.ubuntu.com/ubuntu",
            "https://registry.npmjs.org/vite",
            "https://files.pythonhosted.org/x",
        ):
            self.assertTrue(_is_infra_url(url), url)

    def test_app_urls_are_not_infra(self):
        for url in (
            "http://localhost:1420/",
            "http://127.0.0.1:8000/",
            "http://192.168.2.4:5173/",
            "https://my-app.example.dev/",
        ):
            self.assertFalse(_is_infra_url(url), url)


if __name__ == "__main__":
    unittest.main()
