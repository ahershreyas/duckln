"""Tests for the bundled and cached repo catalog foundation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from duckln.config import ENV_CONFIG_DIR, load_raw_config, resolve_config_paths
from state.repo_catalog import (
    LaunchCatalogMetadataOverride,
    LaunchCatalogOverrides,
    LaunchCatalogSeedEntry,
    RepoCatalogRecord,
    _extract_github_repo_path,
    generate_bundled_repo_catalog,
    initialize_local_repo_catalog_cache,
    load_bundled_repo_catalog,
    load_launch_catalog_overrides,
    load_launch_catalog_seed,
    load_local_repo_catalog,
    load_sorted_local_repo_catalog,
    refresh_local_repo_catalog,
    resolve_bundled_repo_catalog_path,
    resolve_launch_catalog_overrides_path,
    resolve_launch_catalog_seed_path,
    resolve_local_repo_catalog_cache_path,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        return self._payload


class FakeGitHubClient:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.called_urls: list[str] = []
        self.called_headers: list[dict[str, str]] = []
        self.called_follow_redirects: list[bool | None] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
        timeout: float,
        follow_redirects: bool | None = None,
    ) -> FakeResponse:
        self.called_urls.append(url)
        self.called_headers.append(dict(headers))
        self.called_follow_redirects.append(follow_redirects)
        if "q" in params:
            topic = params["q"].split("topic:", 1)[1].split()[0]
            response = self.responses[topic]
        else:
            response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class RepoCatalogTest(unittest.TestCase):
    def test_extract_github_repo_path_preserves_repo_names_with_dots(self) -> None:
        self.assertEqual(
            "ggerganov/llama.cpp",
            _extract_github_repo_path("https://github.com/ggerganov/llama.cpp"),
        )
        self.assertEqual(
            "ggerganov/llama.cpp",
            _extract_github_repo_path("https://github.com/ggerganov/llama.cpp/"),
        )
        self.assertEqual(
            "ggerganov/llama.cpp",
            _extract_github_repo_path("https://github.com/ggerganov/llama.cpp.git"),
        )

    def test_load_bundled_repo_catalog_returns_strict_records(self) -> None:
        records = load_bundled_repo_catalog()

        self.assertTrue(records)
        self.assertGreaterEqual(len(records), 20)
        self.assertLessEqual(len(records), 25)
        self.assertTrue(all(isinstance(record, RepoCatalogRecord) for record in records))
        self.assertNotIn("ollama", {record.name.lower() for record in records})
        self.assertEqual(
            {
                "name",
                "repo_url",
                "stars",
                "description",
                "category",
                "framework",
                "last_updated",
                "warning",
            },
            set(RepoCatalogRecord.__dataclass_fields__),
        )

    def test_load_launch_catalog_overrides_reads_curated_allowlist_blocklist_and_overrides(self) -> None:
        overrides = load_launch_catalog_overrides()

        self.assertNotIn("ollama", overrides.allowlist)
        self.assertIn("ollama", overrides.blocklist)
        self.assertIn("openvino", overrides.blocklist)
        self.assertIn("whisperx", overrides.allowlist)
        self.assertIn("anythingllm", overrides.allowlist)
        self.assertEqual(resolve_launch_catalog_overrides_path().name, "launch_catalog_overrides.json")
        self.assertEqual("Python/vLLM", overrides.overrides["vllm"].framework)
        self.assertEqual("GPU recommended", overrides.overrides["vllm"].warning)
        self.assertEqual("Multi-service setup", overrides.overrides["autogpt"].warning)

    def test_load_launch_catalog_seed_reads_curated_seed_repos(self) -> None:
        seed_entries = load_launch_catalog_seed()

        self.assertGreaterEqual(len(seed_entries), 20)
        self.assertEqual(resolve_launch_catalog_seed_path().name, "launch_catalog_seed.json")
        self.assertEqual("AutoGPT", seed_entries[0].name)
        self.assertEqual("https://github.com/Significant-Gravitas/AutoGPT", seed_entries[0].repo_url)

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

    def test_generate_bundled_repo_catalog_filters_training_repos_and_keeps_practical_repos(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "trainer-kit",
                                "html_url": "https://github.com/example/trainer-kit",
                                "stargazers_count": 90,
                                "description": "Training and fine-tuning toolkit",
                                "language": "Python",
                                "topics": ["machine-learning", "training"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                            {
                                "name": "inference-api",
                                "html_url": "https://github.com/example/inference-api",
                                "stargazers_count": 80,
                                "description": "Inference API for model serving",
                                "language": "Python",
                                "topics": ["machine-learning", "inference", "api"],
                                "updated_at": "2026-03-19T12:00:00Z",
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
                                "name": "open-webui",
                                "html_url": "https://github.com/open-webui/open-webui",
                                "stargazers_count": 70,
                                "description": "A practical demo API for local model serving",
                                "language": "TypeScript",
                                "topics": ["llm"],
                                "updated_at": "2026-03-18T12:00:00Z",
                            },
                            {
                                "name": "grpo-lab",
                                "html_url": "https://github.com/example/grpo-lab",
                                "stargazers_count": 75,
                                "description": "GRPO experiments for post-training",
                                "language": "Python",
                                "topics": ["llm", "grpo"],
                                "updated_at": "2026-03-17T12:00:00Z",
                            },
                        ]
                    },
                ),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
            }
        )

        with patch(
            "state.repo_catalog.load_launch_catalog_seed",
            return_value=(
                LaunchCatalogSeedEntry(name="inference-api", repo_url="https://github.com/example/inference-api"),
                LaunchCatalogSeedEntry(name="open-webui", repo_url="https://github.com/open-webui/open-webui"),
            ),
        ):
            records = generate_bundled_repo_catalog(client=client, per_topic_limit=10)

        self.assertEqual(("inference-api", "open-webui"), tuple(record.name for record in records))
        self.assertEqual((80, 70), tuple(record.stars for record in records))

    def test_generate_bundled_repo_catalog_deduplicates_by_repo_url_and_sorts_descending(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "shared",
                                "html_url": "https://github.com/example/shared",
                                "stargazers_count": 50,
                                "description": "Inference demo",
                                "language": "Python",
                                "topics": ["machine-learning", "inference", "demo"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            }
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
                                "stargazers_count": 50,
                                "description": "Inference demo",
                                "language": "Python",
                                "topics": ["llm", "api"],
                                "updated_at": "2026-03-21T12:00:00Z",
                            },
                            {
                                "name": "llama.cpp",
                                "html_url": "https://github.com/ggerganov/llama.cpp",
                                "stargazers_count": 60,
                                "description": "Port of LLaMA inference in C/C++",
                                "language": "C++",
                                "topics": ["llm"],
                                "updated_at": "2026-03-22T12:00:00Z",
                            },
                        ]
                    },
                ),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
            }
        )

        with patch(
            "state.repo_catalog.load_launch_catalog_seed",
            return_value=(
                LaunchCatalogSeedEntry(name="shared", repo_url="https://github.com/example/shared"),
                LaunchCatalogSeedEntry(name="llama.cpp", repo_url="https://github.com/ggerganov/llama.cpp"),
            ),
        ):
            records = generate_bundled_repo_catalog(client=client, per_topic_limit=10)

        self.assertEqual(("llama.cpp", "shared"), tuple(record.name for record in records))
        self.assertEqual(2, len(records))

    def test_generate_bundled_repo_catalog_excludes_blocked_names_languages_and_learning_repos(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "firecrawl",
                                "html_url": "https://github.com/example/firecrawl",
                                "stargazers_count": 100,
                                "description": "API for web data extraction",
                                "language": "TypeScript",
                                "topics": ["machine-learning", "api"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                            {
                                "name": "practical-python-api",
                                "html_url": "https://github.com/example/practical-python-api",
                                "stargazers_count": 90,
                                "description": "Inference API for deployment",
                                "language": "Python",
                                "topics": ["machine-learning", "inference", "deployment"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                        ]
                    },
                ),
                "deep-learning": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "keras",
                                "html_url": "https://github.com/keras-team/keras",
                                "stargazers_count": 110,
                                "description": "Deep learning framework for Python",
                                "language": "Python",
                                "topics": ["deep-learning", "framework", "api"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                            {
                                "name": "vision-tutorial",
                                "html_url": "https://github.com/example/vision-tutorial",
                                "stargazers_count": 95,
                                "description": "Beginner tutorial and guide for model serving",
                                "language": "Python",
                                "topics": ["deep-learning", "tutorial", "serving"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                            {
                                "name": "frontend-demo",
                                "html_url": "https://github.com/example/frontend-demo",
                                "stargazers_count": 85,
                                "description": "Demo API for inference",
                                "language": "JavaScript",
                                "topics": ["deep-learning", "demo", "api"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                        ]
                    },
                ),
                "llm": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "agents-course",
                                "html_url": "https://github.com/example/agents-course",
                                "stargazers_count": 120,
                                "description": "Course material for building agents",
                                "language": "Python",
                                "topics": ["llm", "course"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            }
                        ]
                    },
                ),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "mediapipe",
                                "html_url": "https://github.com/google-ai-edge/mediapipe",
                                "stargazers_count": 130,
                                "description": "Framework for building multimodal pipelines",
                                "language": "C++",
                                "topics": ["computer-vision", "framework"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            }
                        ]
                    },
                ),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "datasets",
                                "html_url": "https://github.com/huggingface/datasets",
                                "stargazers_count": 115,
                                "description": "Dataset library for machine learning",
                                "language": "Python",
                                "topics": ["huggingface", "datasets"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            }
                        ]
                    },
                ),
            }
        )

        with patch(
            "state.repo_catalog.load_launch_catalog_seed",
            return_value=(LaunchCatalogSeedEntry(name="practical-python-api", repo_url="https://github.com/example/practical-python-api"),),
        ):
            records = generate_bundled_repo_catalog(client=client, per_topic_limit=10)

        self.assertEqual(("practical-python-api",), tuple(record.name for record in records))

    def test_generate_bundled_repo_catalog_applies_launch_category_overrides(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(200, {"items": []}),
                "deep-learning": FakeResponse(200, {"items": []}),
                "llm": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "LocalAI",
                                "html_url": "https://github.com/mudler/LocalAI",
                                "stargazers_count": 40,
                                "description": "Run local models with an OpenAI-compatible API",
                                "language": "Go",
                                "topics": ["llm", "api"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                            {
                                "name": "ollama",
                                "html_url": "https://github.com/ollama/ollama",
                                "stargazers_count": 39,
                                "description": "A practical local model runner",
                                "language": "Go",
                                "topics": ["llm"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                            {
                                "name": "vllm",
                                "html_url": "https://github.com/vllm-project/vllm",
                                "stargazers_count": 41,
                                "description": "A high-throughput and memory-efficient inference and serving engine for LLMs",
                                "language": "Python",
                                "topics": ["llm", "pytorch", "inference", "serving"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                        ]
                    },
                ),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "openvino",
                                "html_url": "https://github.com/openvinotoolkit/openvino",
                                "stargazers_count": 38,
                                "description": "Toolkit for optimizing and deploying AI inference",
                                "language": "C++",
                                "topics": ["computer-vision", "inference", "deployment"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            }
                        ]
                    },
                ),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
            }
        )

        with (
            patch(
                "state.repo_catalog.load_launch_catalog_seed",
                return_value=(
                    LaunchCatalogSeedEntry(name="LocalAI", repo_url="https://github.com/mudler/LocalAI"),
                    LaunchCatalogSeedEntry(name="ollama", repo_url="https://github.com/ollama/ollama"),
                    LaunchCatalogSeedEntry(name="vllm", repo_url="https://github.com/vllm-project/vllm"),
                    LaunchCatalogSeedEntry(name="openvino", repo_url="https://github.com/openvinotoolkit/openvino"),
                ),
            ),
            patch(
                "state.repo_catalog.load_launch_catalog_overrides",
                return_value=LaunchCatalogOverrides(
                    allowlist=frozenset(),
                    blocklist=frozenset(),
                    overrides={},
                ),
            ),
        ):
            records = generate_bundled_repo_catalog(client=client, per_topic_limit=10)
        categories = {record.name: record.category for record in records}
        frameworks = {record.name: record.framework for record in records}

        self.assertEqual("LLM", categories["LocalAI"])
        self.assertEqual("LLM", categories["ollama"])
        self.assertEqual("LLM", categories["vllm"])
        self.assertEqual("Computer Vision", categories["openvino"])
        self.assertEqual("Go/Local Runtime", frameworks["LocalAI"])
        self.assertEqual("Go/Ollama", frameworks["ollama"])
        self.assertEqual("Python/vLLM", frameworks["vllm"])
        self.assertEqual("C++/OpenVINO", frameworks["openvino"])

    def test_generate_bundled_repo_catalog_applies_curated_allowlist_blocklist_and_warning_overrides(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(200, {"items": []}),
                "deep-learning": FakeResponse(200, {"items": []}),
                "llm": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "ollama",
                                "html_url": "https://github.com/ollama/ollama",
                                "stargazers_count": 100,
                                "description": "Run models locally.",
                                "language": "Go",
                                "topics": ["llm"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                            {
                                "name": "openvino",
                                "html_url": "https://github.com/openvinotoolkit/openvino",
                                "stargazers_count": 90,
                                "description": "Toolkit for optimizing and deploying AI inference",
                                "language": "C++",
                                "topics": ["llm", "inference"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                            {
                                "name": "vllm",
                                "html_url": "https://github.com/vllm-project/vllm",
                                "stargazers_count": 80,
                                "description": "A high-throughput and memory-efficient inference and serving engine for LLMs",
                                "language": "Python",
                                "topics": ["llm", "pytorch", "inference", "serving"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            },
                        ]
                    },
                ),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
            }
        )

        with (
            patch(
                "state.repo_catalog.load_launch_catalog_seed",
                return_value=(
                    LaunchCatalogSeedEntry(name="ollama", repo_url="https://github.com/ollama/ollama"),
                    LaunchCatalogSeedEntry(name="openvino", repo_url="https://github.com/openvinotoolkit/openvino"),
                    LaunchCatalogSeedEntry(name="vllm", repo_url="https://github.com/vllm-project/vllm"),
                ),
            ),
            patch(
                "state.repo_catalog.load_launch_catalog_overrides",
                return_value=LaunchCatalogOverrides(
                    allowlist=frozenset({"ollama"}),
                    blocklist=frozenset({"openvino"}),
                    overrides={
                        "ollama": LaunchCatalogMetadataOverride(category="LLM", framework="Go/Ollama"),
                        "vllm": LaunchCatalogMetadataOverride(category="LLM", framework="Python/vLLM", warning="GPU recommended"),
                    },
                ),
            ),
        ):
            records = generate_bundled_repo_catalog(client=client, per_topic_limit=10)

        self.assertEqual(("ollama", "vllm"), tuple(record.name for record in records))
        warnings = {record.name: record.warning for record in records}
        frameworks = {record.name: record.framework for record in records}
        self.assertIsNone(warnings["ollama"])
        self.assertEqual("GPU recommended", warnings["vllm"])
        self.assertEqual("Go/Ollama", frameworks["ollama"])

    def test_generate_bundled_repo_catalog_uses_seed_repo_even_without_topic_match(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(200, {"items": []}),
                "deep-learning": FakeResponse(200, {"items": []}),
                "llm": FakeResponse(200, {"items": []}),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
                "https://api.github.com/repos/example/topic-hit": FakeResponse(
                    200,
                    {
                        "name": "topic-hit",
                        "html_url": "https://github.com/example/topic-hit",
                        "stargazers_count": 50,
                        "description": "Inference demo",
                        "language": "Python",
                        "topics": ["machine-learning", "inference"],
                        "updated_at": "2026-03-20T12:00:00Z",
                    },
                ),
                "https://api.github.com/repos/example/seed-only": FakeResponse(
                    200,
                    {
                        "name": "seed-only",
                        "html_url": "https://github.com/example/seed-only",
                        "stargazers_count": 42,
                        "description": "Seed-first repo enriched from direct GitHub lookup.",
                        "language": "Python",
                        "topics": ["llm"],
                        "updated_at": "2026-03-20T12:00:00Z",
                    },
                ),
            }
        )

        with (
            patch(
                "state.repo_catalog.load_launch_catalog_seed",
                return_value=(
                    LaunchCatalogSeedEntry(
                        name="seed-only",
                        repo_url="https://github.com/example/seed-only",
                    ),
                ),
            ),
            patch(
                "state.repo_catalog.load_launch_catalog_overrides",
                return_value=LaunchCatalogOverrides(
                    allowlist=frozenset({"seed-only"}),
                    blocklist=frozenset(),
                    overrides={
                        "seed-only": LaunchCatalogMetadataOverride(category="LLM", framework="Python"),
                    },
                ),
            ),
        ):
            records = generate_bundled_repo_catalog(client=client, per_topic_limit=10)

        self.assertEqual(1, len(records))
        self.assertEqual("seed-only", records[0].name)
        self.assertEqual(42, records[0].stars)

    def test_generate_bundled_repo_catalog_fetches_seed_metadata_from_github_api_repo_endpoint(self) -> None:
        seed_repo_url = "https://github.com/ggerganov/llama.cpp.git"
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(200, {"items": []}),
                "deep-learning": FakeResponse(200, {"items": []}),
                "llm": FakeResponse(200, {"items": []}),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
                "https://api.github.com/repos/ggerganov/llama.cpp": FakeResponse(
                    200,
                    {
                        "name": "llama.cpp",
                        "html_url": "https://github.com/ggerganov/llama.cpp",
                        "stargazers_count": 77,
                        "description": "Local LLM inference runtime.",
                        "language": "C++",
                        "topics": ["llm"],
                        "updated_at": "2026-03-20T12:00:00Z",
                    },
                ),
            }
        )

        with (
            patch(
                "state.repo_catalog.load_launch_catalog_seed",
                return_value=(
                    LaunchCatalogSeedEntry(
                        name="llama.cpp",
                        repo_url=seed_repo_url,
                    ),
                ),
            ),
            patch(
                "state.repo_catalog.load_launch_catalog_overrides",
                return_value=LaunchCatalogOverrides(
                    allowlist=frozenset({"llama.cpp"}),
                    blocklist=frozenset(),
                    overrides={
                        "llama.cpp": LaunchCatalogMetadataOverride(
                            category="LLM",
                            framework="C++/Local Runtime",
                        ),
                    },
                ),
            ),
        ):
            records = generate_bundled_repo_catalog(client=client, per_topic_limit=10)

        self.assertEqual(("llama.cpp",), tuple(record.name for record in records))
        self.assertEqual(seed_repo_url, records[0].repo_url)
        self.assertIn("https://api.github.com/repos/ggerganov/llama.cpp", client.called_urls)
        self.assertTrue(all(url.startswith("https://api.github.com/") for url in client.called_urls))
        self.assertTrue(all(follow_redirects is True for follow_redirects in client.called_follow_redirects))

    def test_generate_bundled_repo_catalog_uses_authenticated_headers_for_topic_and_seed_requests(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(
                    200,
                    {
                        "items": [
                            {
                                "name": "topic-hit",
                                "html_url": "https://github.com/example/topic-hit",
                                "stargazers_count": 50,
                                "description": "Inference demo",
                                "language": "Python",
                                "topics": ["machine-learning", "inference"],
                                "updated_at": "2026-03-20T12:00:00Z",
                            }
                        ]
                    },
                ),
                "deep-learning": FakeResponse(200, {"items": []}),
                "llm": FakeResponse(200, {"items": []}),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
                "https://api.github.com/repos/example/seed-only": FakeResponse(
                    200,
                    {
                        "name": "seed-only",
                        "html_url": "https://github.com/example/seed-only",
                        "stargazers_count": 42,
                        "description": "Seed repo metadata",
                        "language": "Python",
                        "topics": ["llm"],
                        "updated_at": "2026-03-20T12:00:00Z",
                    },
                ),
            }
        )

        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}, clear=False),
            patch(
                "state.repo_catalog.load_launch_catalog_seed",
                return_value=(
                    LaunchCatalogSeedEntry(name="topic-hit", repo_url="https://github.com/example/topic-hit"),
                    LaunchCatalogSeedEntry(name="seed-only", repo_url="https://github.com/example/seed-only"),
                ),
            ),
            patch(
                "state.repo_catalog.load_launch_catalog_overrides",
                return_value=LaunchCatalogOverrides(
                    allowlist=frozenset({"topic-hit", "seed-only"}),
                    blocklist=frozenset(),
                    overrides={},
                ),
            ),
        ):
            records = generate_bundled_repo_catalog(client=client, per_topic_limit=10)

        self.assertEqual(("topic-hit", "seed-only"), tuple(record.name for record in records))
        self.assertIn("https://api.github.com/repos/example/topic-hit", client.called_urls)
        self.assertIn("https://api.github.com/repos/example/seed-only", client.called_urls)
        self.assertTrue(client.called_headers)
        self.assertTrue(all(headers.get("Authorization") == "Bearer test-token" for headers in client.called_headers))
        self.assertTrue(all(headers.get("Accept") == "application/vnd.github+json" for headers in client.called_headers))
        self.assertTrue(all(headers.get("User-Agent") == "Duckln" for headers in client.called_headers))

    def test_generate_bundled_repo_catalog_includes_github_403_body_in_error_message(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(200, {"items": []}),
                "deep-learning": FakeResponse(200, {"items": []}),
                "llm": FakeResponse(200, {"items": []}),
                "stable-diffusion": FakeResponse(
                    403,
                    {"message": "API rate limit exceeded"},
                    text='{"message":"API rate limit exceeded"}',
                ),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
            }
        )

        with (
            patch("sys.stderr"),
            self.assertRaisesRegex(
                Exception,
                "GitHub topic fetch failed for 'stable-diffusion' \\(HTTP 403\\): \\{\"message\":\"API rate limit exceeded\"\\}",
            ),
        ):
            generate_bundled_repo_catalog(client=client, per_topic_limit=10)

    def test_generate_bundled_repo_catalog_includes_github_403_json_body_when_text_is_empty(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(200, {"items": []}),
                "deep-learning": FakeResponse(200, {"items": []}),
                "llm": FakeResponse(200, {"items": []}),
                "stable-diffusion": FakeResponse(
                    403,
                    {"message": "Resource protected by organization SSO"},
                ),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
            }
        )

        with (
            patch("sys.stderr"),
            self.assertRaisesRegex(
                Exception,
                "GitHub topic fetch failed for 'stable-diffusion' \\(HTTP 403\\): \\{\"message\":\"Resource protected by organization SSO\"\\}",
            ),
        ):
            generate_bundled_repo_catalog(client=client, per_topic_limit=10)

    def test_generate_bundled_repo_catalog_includes_github_403_body_for_seed_repo_fetch(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(200, {"items": []}),
                "deep-learning": FakeResponse(200, {"items": []}),
                "llm": FakeResponse(200, {"items": []}),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
                "https://api.github.com/repos/example/private-seed": FakeResponse(
                    403,
                    {"message": "Secondary rate limit"},
                    text='{"message":"Secondary rate limit"}',
                ),
            }
        )

        with (
            patch(
                "state.repo_catalog.load_launch_catalog_seed",
                return_value=(
                    LaunchCatalogSeedEntry(name="private-seed", repo_url="https://github.com/example/private-seed"),
                ),
            ),
            patch(
                "state.repo_catalog.load_launch_catalog_overrides",
                return_value=LaunchCatalogOverrides(
                    allowlist=frozenset({"private-seed"}),
                    blocklist=frozenset(),
                    overrides={},
                ),
            ),
            patch("sys.stderr"),
        ):
            with self.assertRaisesRegex(
                Exception,
                "GitHub repo fetch failed for 'https://github.com/example/private-seed' \\(HTTP 403\\): \\{\"message\":\"Secondary rate limit\"\\}",
            ):
                generate_bundled_repo_catalog(client=client, per_topic_limit=10)

    def test_generate_bundled_repo_catalog_skips_failed_seed_repo_and_keeps_successful_records(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(200, {"items": []}),
                "deep-learning": FakeResponse(200, {"items": []}),
                "llm": FakeResponse(200, {"items": []}),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
                "https://api.github.com/repos/example/good-seed": FakeResponse(
                    200,
                    {
                        "name": "good-seed",
                        "html_url": "https://github.com/example/good-seed",
                        "stargazers_count": 42,
                        "description": "Working repo metadata",
                        "language": "Python",
                        "topics": ["llm"],
                        "updated_at": "2026-03-20T12:00:00Z",
                    },
                ),
                "https://api.github.com/repos/example/bad-seed": FakeResponse(
                    301,
                    {"message": "Moved permanently"},
                    text='{"message":"Moved permanently"}',
                ),
            }
        )

        with (
            patch(
                "state.repo_catalog.load_launch_catalog_seed",
                return_value=(
                    LaunchCatalogSeedEntry(name="good-seed", repo_url="https://github.com/example/good-seed"),
                    LaunchCatalogSeedEntry(name="bad-seed", repo_url="https://github.com/example/bad-seed"),
                ),
            ),
            patch(
                "state.repo_catalog.load_launch_catalog_overrides",
                return_value=LaunchCatalogOverrides(
                    allowlist=frozenset({"good-seed", "bad-seed"}),
                    blocklist=frozenset(),
                    overrides={},
                ),
            ),
            patch("sys.stderr"),
        ):
            records = generate_bundled_repo_catalog(client=client, per_topic_limit=10)

        self.assertEqual(("good-seed",), tuple(record.name for record in records))
        self.assertIn("https://api.github.com/repos/example/good-seed", client.called_urls)
        self.assertIn("https://api.github.com/repos/example/bad-seed", client.called_urls)
        self.assertNotIn("https://github.com/example/good-seed", client.called_urls)
        self.assertNotIn("https://github.com/example/bad-seed", client.called_urls)

    def test_generate_bundled_repo_catalog_fails_only_when_zero_seed_repos_are_fetched(self) -> None:
        client = FakeGitHubClient(
            {
                "machine-learning": FakeResponse(200, {"items": []}),
                "deep-learning": FakeResponse(200, {"items": []}),
                "llm": FakeResponse(200, {"items": []}),
                "stable-diffusion": FakeResponse(200, {"items": []}),
                "computer-vision": FakeResponse(200, {"items": []}),
                "pytorch": FakeResponse(200, {"items": []}),
                "huggingface": FakeResponse(200, {"items": []}),
                "https://api.github.com/repos/example/bad-seed": FakeResponse(
                    301,
                    {"message": "Moved permanently"},
                    text='{"message":"Moved permanently"}',
                ),
            }
        )

        with (
            patch(
                "state.repo_catalog.load_launch_catalog_seed",
                return_value=(
                    LaunchCatalogSeedEntry(name="bad-seed", repo_url="https://github.com/example/bad-seed"),
                ),
            ),
            patch(
                "state.repo_catalog.load_launch_catalog_overrides",
                return_value=LaunchCatalogOverrides(
                    allowlist=frozenset({"bad-seed"}),
                    blocklist=frozenset(),
                    overrides={},
                ),
            ),
            patch("sys.stderr"),
            self.assertRaisesRegex(
                Exception,
                "Catalog generation failed - zero repos fetched successfully.*GitHub repo fetch failed for 'https://github.com/example/bad-seed' \\(HTTP 301\\)",
            ),
        ):
            generate_bundled_repo_catalog(client=client, per_topic_limit=10)


if __name__ == "__main__":
    unittest.main()
