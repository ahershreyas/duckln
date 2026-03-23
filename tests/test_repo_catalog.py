"""Tests for the bundled and cached repo catalog foundation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from duckln.config import ENV_CONFIG_DIR, load_raw_config, resolve_config_paths
from state.repo_catalog import (
    RepoCatalogRecord,
    initialize_local_repo_catalog_cache,
    load_bundled_repo_catalog,
    load_local_repo_catalog,
    load_sorted_local_repo_catalog,
    refresh_local_repo_catalog,
    resolve_bundled_repo_catalog_path,
    resolve_local_repo_catalog_cache_path,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeGitHubClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses

    def get(self, url: str, *, headers: dict[str, str], params: dict[str, str], timeout: float) -> FakeResponse:
        topic = params["q"].split("topic:", 1)[1].split()[0]
        response = self.responses[topic]
        if isinstance(response, Exception):
            raise response
        return response


class RepoCatalogTest(unittest.TestCase):
    def test_load_bundled_repo_catalog_returns_strict_records(self) -> None:
        records = load_bundled_repo_catalog()

        self.assertTrue(records)
        self.assertTrue(all(isinstance(record, RepoCatalogRecord) for record in records))
        self.assertEqual(
            {
                "name",
                "repo_url",
                "stars",
                "description",
                "category",
                "framework",
                "last_updated",
            },
            set(RepoCatalogRecord.__dataclass_fields__),
        )

    def test_initialize_local_repo_catalog_cache_copies_bundled_asset_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)

            cache_path = initialize_local_repo_catalog_cache(config_dir)
            self.assertTrue(cache_path.exists())
            self.assertEqual(
                resolve_bundled_repo_catalog_path().read_text(encoding="utf-8"),
                cache_path.read_text(encoding="utf-8"),
            )

            cache_path.write_text('{"repos": []}\n', encoding="utf-8")
            initialize_local_repo_catalog_cache(config_dir)
            self.assertEqual('{"repos": []}\n', cache_path.read_text(encoding="utf-8"))

    def test_load_local_repo_catalog_reads_from_user_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-03-23",
                        "repos": [
                            {
                                "name": "alpha",
                                "repo_url": "https://example.com/alpha",
                                "stars": 12,
                                "description": "Alpha repo",
                                "category": "LLM",
                                "framework": "Python",
                                "last_updated": "2026-03-22",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            records = load_local_repo_catalog(config_dir)

            self.assertEqual(1, len(records))
            self.assertEqual("alpha", records[0].name)
            self.assertEqual(12, records[0].stars)

    def test_load_sorted_local_repo_catalog_sorts_by_stars_descending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "last_updated": "2026-03-23",
                        "repos": [
                            {
                                "name": "mid",
                                "repo_url": "https://example.com/mid",
                                "stars": 20,
                                "description": "Mid repo",
                                "category": "Audio",
                                "framework": "Python",
                                "last_updated": "2026-03-20",
                            },
                            {
                                "name": "top",
                                "repo_url": "https://example.com/top",
                                "stars": 40,
                                "description": "Top repo",
                                "category": "LLM",
                                "framework": "C++",
                                "last_updated": "2026-03-21",
                            },
                            {
                                "name": "low",
                                "repo_url": "https://example.com/low",
                                "stars": 5,
                                "description": "Low repo",
                                "category": "Vision",
                                "framework": "Rust",
                                "last_updated": "2026-03-19",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            records = load_sorted_local_repo_catalog(config_dir)

            self.assertEqual(("top", "mid", "low"), tuple(record.name for record in records))

    def test_runtime_storage_initializes_repo_catalog_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_config_paths({ENV_CONFIG_DIR: temp_dir})

            load_raw_config(paths)

            self.assertTrue(resolve_local_repo_catalog_cache_path(Path(temp_dir)).exists())

    def test_refresh_local_repo_catalog_overwrites_cache_after_successful_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            initialize_local_repo_catalog_cache(config_dir)
            client = FakeGitHubClient(
                {
                    "machine-learning": FakeResponse(
                        200,
                        {
                            "items": [
                                {
                                    "name": "alpha",
                                    "html_url": "https://github.com/example/alpha",
                                    "stargazers_count": 50,
                                    "description": "Alpha ML repo",
                                    "language": "Python",
                                    "topics": ["machine-learning", "pytorch"],
                                    "updated_at": "2026-03-20T12:00:00Z",
                                },
                                {
                                    "name": "shared",
                                    "html_url": "https://github.com/example/shared",
                                    "stargazers_count": 10,
                                    "description": "Shared repo",
                                    "language": "Python",
                                    "topics": ["machine-learning"],
                                    "updated_at": "2026-03-18T12:00:00Z",
                                },
                            ]
                        },
                    ),
                    "deep-learning": FakeResponse(200, {"items": []}),
                    "llm": FakeResponse(
                        200,
                        {
                            "items": [
                                {
                                    "name": "shared",
                                    "html_url": "https://github.com/example/shared",
                                    "stargazers_count": 25,
                                    "description": "Shared repo updated",
                                    "language": "Python",
                                    "topics": ["llm", "huggingface"],
                                    "updated_at": "2026-03-21T12:00:00Z",
                                }
                            ]
                        },
                    ),
                    "stable-diffusion": FakeResponse(200, {"items": []}),
                    "computer-vision": FakeResponse(200, {"items": []}),
                    "pytorch": FakeResponse(200, {"items": []}),
                    "huggingface": FakeResponse(200, {"items": []}),
                }
            )

            result = refresh_local_repo_catalog(config_dir, client=client, per_topic_limit=5)

            self.assertTrue(result.ok)
            self.assertEqual(("alpha", "shared"), tuple(record.name for record in result.records))
            self.assertEqual(25, result.records[1].stars)
            self.assertEqual("LLM", result.records[1].category)
            self.assertEqual("Python/Hugging Face", result.records[1].framework)

            cached_payload = json.loads(resolve_local_repo_catalog_cache_path(config_dir).read_text(encoding="utf-8"))
            self.assertEqual(("alpha", "shared"), tuple(item["name"] for item in cached_payload["repos"]))

    def test_refresh_local_repo_catalog_preserves_previous_cache_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            cache_path = resolve_local_repo_catalog_cache_path(config_dir)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            original_payload = {
                "last_updated": "2026-03-20",
                "repos": [
                    {
                        "name": "cached",
                        "repo_url": "https://example.com/cached",
                        "stars": 7,
                        "description": "Cached repo",
                        "category": "LLM",
                        "framework": "Python",
                        "last_updated": "2026-03-19",
                    }
                ],
            }
            cache_path.write_text(json.dumps(original_payload) + "\n", encoding="utf-8")

            client = FakeGitHubClient(
                {
                    "machine-learning": FakeResponse(200, {"items": []}),
                    "deep-learning": FakeResponse(200, {"items": []}),
                    "llm": FakeResponse(503, {"message": "Service unavailable"}),
                    "stable-diffusion": FakeResponse(200, {"items": []}),
                    "computer-vision": FakeResponse(200, {"items": []}),
                    "pytorch": FakeResponse(200, {"items": []}),
                    "huggingface": FakeResponse(200, {"items": []}),
                }
            )

            result = refresh_local_repo_catalog(config_dir, client=client, per_topic_limit=5)

            self.assertFalse(result.ok)
            self.assertIn("Repo catalog refresh failed:", result.message)
            self.assertEqual(("cached",), tuple(record.name for record in result.records))
            self.assertEqual(original_payload, json.loads(cache_path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
