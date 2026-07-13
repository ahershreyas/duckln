"""Plan 74 — Duckln reasons about the repo's stack: scope prerequisites to the
detected family instead of installing every tool the README mentions."""

from __future__ import annotations

import unittest

from duckln.repo_bringup import RepoBringUpStep, RepoFamily, _scope_prereqs_to_family


def _steps():
    return (
        RepoBringUpStep(purpose="Clone X", command="git clone --depth 1 https://x ."),
        RepoBringUpStep(purpose="Ensure python (>=3.13) is installed before setup runs", command="sudo apt install -y python3"),
        RepoBringUpStep(purpose="Ensure rust is installed before setup runs", command="curl https://sh.rustup.rs | sh"),
        RepoBringUpStep(purpose="Ensure uv is installed before setup runs", command="curl https://astral.sh/uv | sh"),
        RepoBringUpStep(purpose="Ensure go is installed before setup runs", command="sudo apt install -y golang"),
        RepoBringUpStep(purpose="Ensure docker is installed before setup runs", command="sudo apt install -y docker.io"),
        RepoBringUpStep(purpose="Ensure node is installed before setup runs", command="sudo apt install -y nodejs npm"),
        RepoBringUpStep(purpose="Run README install command", command="npm ci"),
        RepoBringUpStep(purpose="Run X", command="npm run dev"),
    )


class PrereqScopingTest(unittest.TestCase):
    def test_node_repo_drops_unrelated_runtimes(self) -> None:
        kept = _scope_prereqs_to_family(
            steps=_steps(), repo_family=RepoFamily.NODE_TYPESCRIPT,
            detected_files=("package.json", "package-lock.json"),
        )
        purposes = "\n".join(s.purpose for s in kept)
        self.assertIn("Ensure node is installed", purposes)
        for unrelated in ("python", "rust", "uv", "go", "docker"):
            self.assertNotIn(f"Ensure {unrelated} is installed", purposes)
        # Real commands (clone, npm) are always kept.
        self.assertTrue(any("git clone" in (s.command or "") for s in kept))
        self.assertTrue(any(s.command == "npm ci" for s in kept))

    def test_python_repo_keeps_python_drops_node(self) -> None:
        steps = (
            RepoBringUpStep(purpose="Ensure python is installed before setup runs", command="sudo apt install -y python3"),
            RepoBringUpStep(purpose="Ensure node is installed before setup runs", command="sudo apt install -y nodejs"),
            RepoBringUpStep(purpose="Install deps", command="pip install -r requirements.txt"),
        )
        kept = _scope_prereqs_to_family(steps=steps, repo_family=RepoFamily.PYTHON, detected_files=("requirements.txt",))
        purposes = "\n".join(s.purpose for s in kept)
        self.assertIn("Ensure python is installed", purposes)
        self.assertNotIn("Ensure node is installed", purposes)

    def test_docker_kept_when_dockerfile_present(self) -> None:
        steps = (
            RepoBringUpStep(purpose="Ensure docker is installed before setup runs", command="sudo apt install -y docker.io"),
            RepoBringUpStep(purpose="Build image", command="docker build -t app ."),
        )
        kept = _scope_prereqs_to_family(steps=steps, repo_family=RepoFamily.NODE_TYPESCRIPT, detected_files=("Dockerfile", "package.json"))
        self.assertTrue(any("Ensure docker is installed" in s.purpose for s in kept))

    def test_command_referenced_tool_kept_even_if_not_family(self) -> None:
        # A Node repo whose build genuinely invokes python (node-gyp) keeps python.
        steps = (
            RepoBringUpStep(purpose="Ensure python is installed before setup runs", command="sudo apt install -y python3"),
            RepoBringUpStep(purpose="Install", command="npm ci"),
            RepoBringUpStep(purpose="Native build", command="python3 build_native.py"),
        )
        kept = _scope_prereqs_to_family(steps=steps, repo_family=RepoFamily.NODE_TYPESCRIPT, detected_files=("package.json",))
        self.assertTrue(any("Ensure python is installed" in s.purpose for s in kept))


if __name__ == "__main__":
    unittest.main()
