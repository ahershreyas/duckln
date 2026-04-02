"""Bundled and cached repo catalog helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any
from urllib.parse import urlparse

from agent.memory import resolve_agent_memory_paths


REPO_CATALOG_FILE_NAME = "repos.json"
LAUNCH_CATALOG_SEED_FILE_NAME = "launch_catalog_seed.json"
LAUNCH_CATALOG_OVERRIDES_FILE_NAME = "launch_catalog_overrides.json"
GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_ACCEPT = "application/vnd.github+json"
GITHUB_API_USER_AGENT = "Duckln"
GITHUB_REPO_TOPICS = (
    "machine-learning",
    "deep-learning",
    "llm",
    "stable-diffusion",
    "computer-vision",
    "pytorch",
    "huggingface",
)
DEFAULT_TOPIC_FETCH_LIMIT = 10
LAUNCH_PRIORITY_REPO_NAMES = frozenset(
    {
        "whisper",
        "llama.cpp",
        "stable-diffusion-webui",
        "ollama",
        "open-webui",
        "comfyui",
        "vllm",
        "ultralytics",
        "yolov5",
        "localai",
        "diffusers",
        "invokeai",
        "real-time-voice-cloning",
        "speechbrain",
        "autogpt",
        "diffusionbee-stable-diffusion-ui",
    }
)
EXCLUDED_BUNDLED_REPO_NAMES = frozenset(
    {
        "tensorflow",
        "prompts.chat",
        "dify",
        "everything-claude-code",
        "firecrawl",
        "pytorch",
        "llms-from-scratch",
        "ml-for-beginners",
        "netdata",
        "cs-video-courses",
        "llm-course",
        "d2l-zh",
        "prompt-engineering-guide",
        "annotated_deep_learning_paper_implementations",
        "ai-for-beginners",
        "applied-ml",
        "ruflo",
        "langchain4j",
        "awesome-ai-painting",
        "paper2gui",
        "skorch",
        "swarms",
        "ai-research-skills",
        "agents-course",
        "datasets",
        "keras",
        "opencv",
        "mediapipe",
    }
)
TRAINING_SIGNALS = (
    "training",
    "fine-tuning",
    "fine tuning",
    "pretraining",
    "pre-training",
    "grpo",
)
PRACTICAL_RUNNABLE_SIGNALS = (
    "inference",
    "serving",
    "demo",
    "api",
    "deployment",
)
NON_RUNNABLE_LEARNING_SIGNALS = (
    "course",
    "tutorial",
    "awesome",
    "guide",
    "papers",
    "resources",
    "beginners",
    "textbook",
    "from scratch",
    "from-scratch",
)
EXCLUDED_PRIMARY_LANGUAGES = frozenset(
    {
        "html",
        "javascript",
        "typescript",
        "java",
        "c",
        "unknown",
    }
)
BUNDLED_CATEGORY_OVERRIDES = {
    "localai": "LLM",
    "openvino": "Computer Vision",
    "ollama": "LLM",
    "vllm": "LLM",
    "stable-diffusion-webui": "Stable Diffusion",
    "comfyui": "Stable Diffusion",
    "invokeai": "Stable Diffusion",
    "diffusionbee-stable-diffusion-ui": "Stable Diffusion",
    "ultralytics": "Computer Vision",
    "yolov5": "Computer Vision",
    "whisper": "Audio",
    "speechbrain": "Audio",
    "real-time-voice-cloning": "Audio",
}
BUNDLED_FRAMEWORK_OVERRIDES = {
    "localai": "Go/Local Runtime",
    "ollama": "Go/Ollama",
    "openvino": "C++/OpenVINO",
    "vllm": "Python/vLLM",
    "diffusers": "Python/Diffusers",
}
AUDIO_CATEGORY_SIGNALS = ("audio", "voice", "speech", "tts", "asr", "music", "cloning")
COMPUTER_VISION_CATEGORY_SIGNALS = ("vision", "yolo", "image", "opencv", "openvino", "detection", "segmentation")
STABLE_DIFFUSION_CATEGORY_SIGNALS = ("stable diffusion", "diffusion", "image generation")
LLM_CATEGORY_SIGNALS = ("llm", "chat", "assistant", "openai-compatible", "openai compatible", "model runner")


class RepoCatalogRefreshError(RuntimeError):
    """Raised when a live repo catalog refresh cannot complete safely."""


@dataclass(frozen=True)
class RepoCatalogRecord:
    """Minimal repo catalog record used for dropdown selection."""

    name: str
    repo_url: str
    stars: int
    description: str
    category: str
    framework: str
    last_updated: str
    warning: str | None = None


@dataclass(frozen=True)
class LaunchCatalogMetadataOverride:
    """Curated metadata overrides for bundled launch catalog repos."""

    category: str | None = None
    framework: str | None = None
    warning: str | None = None


@dataclass(frozen=True)
class LaunchCatalogOverrides:
    """Curated allowlist, blocklist, and metadata overrides for launch catalog generation."""

    allowlist: frozenset[str]
    blocklist: frozenset[str]
    overrides: dict[str, LaunchCatalogMetadataOverride]


@dataclass(frozen=True)
class LaunchCatalogSeedEntry:
    """Curated seed entry for the bundled launch catalog."""

    name: str
    repo_url: str


@dataclass(frozen=True)
class RepoCatalogRefreshResult:
    """Result of a manual repo catalog refresh."""

    ok: bool
    message: str
    records: tuple[RepoCatalogRecord, ...]


def resolve_bundled_repo_catalog_path() -> Path:
    """Return the bundled repo catalog asset path."""

    return Path(__file__).resolve().parents[1] / "assets" / REPO_CATALOG_FILE_NAME


def resolve_launch_catalog_seed_path() -> Path:
    """Return the curated launch catalog seed asset path."""

    return Path(__file__).resolve().parents[1] / "assets" / LAUNCH_CATALOG_SEED_FILE_NAME


def resolve_launch_catalog_overrides_path() -> Path:
    """Return the curated launch catalog overrides asset path."""

    return Path(__file__).resolve().parents[1] / "assets" / LAUNCH_CATALOG_OVERRIDES_FILE_NAME


def resolve_local_repo_catalog_cache_path(config_dir: Path) -> Path:
    """Return the per-user cached repo catalog path under Duckln knowledge."""

    return resolve_agent_memory_paths(config_dir).knowledge_dir / REPO_CATALOG_FILE_NAME


def load_bundled_repo_catalog() -> tuple[RepoCatalogRecord, ...]:
    """Load repo records from the bundled catalog asset."""

    return _load_repo_catalog_file(resolve_bundled_repo_catalog_path())


def load_launch_catalog_overrides() -> LaunchCatalogOverrides:
    """Load curated launch catalog overrides for bundled generation."""

    payload = json.loads(resolve_launch_catalog_overrides_path().read_text(encoding="utf-8"))
    return _parse_launch_catalog_overrides(payload)


def load_launch_catalog_seed() -> tuple[LaunchCatalogSeedEntry, ...]:
    """Load the curated launch catalog seed used for bundled generation."""

    payload = json.loads(resolve_launch_catalog_seed_path().read_text(encoding="utf-8"))
    return _parse_launch_catalog_seed(payload)


def initialize_local_repo_catalog_cache(config_dir: Path) -> Path:
    """Copy the bundled catalog into the local cache if it is missing."""

    cache_path = resolve_local_repo_catalog_cache_path(config_dir)
    if cache_path.exists():
        return cache_path

    bundled_path = resolve_bundled_repo_catalog_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundled_path, cache_path)
    return cache_path


def load_local_repo_catalog(config_dir: Path) -> tuple[RepoCatalogRecord, ...]:
    """Load repo records from the local Duckln cache."""

    cache_path = initialize_local_repo_catalog_cache(config_dir)
    return _load_repo_catalog_file(cache_path)


def load_sorted_local_repo_catalog(config_dir: Path) -> tuple[RepoCatalogRecord, ...]:
    """Load cached repo records sorted for dropdown use."""

    records = load_local_repo_catalog(config_dir)
    return tuple(sorted(records, key=lambda item: (-item.stars, item.name.lower())))


def generate_bundled_repo_catalog(
    *,
    client: Any | None = None,
    per_topic_limit: int = DEFAULT_TOPIC_FETCH_LIMIT,
) -> tuple[RepoCatalogRecord, ...]:
    """Generate the launch-ready bundled repo catalog from GitHub topic data."""

    if per_topic_limit <= 0:
        raise ValueError("Bundled repo catalog generation requires a positive per-topic limit.")

    launch_overrides = load_launch_catalog_overrides()
    seed_entries = load_launch_catalog_seed()
    warnings: list[str] = []
    topic_candidates = _fetch_topic_candidate_payloads(
        client=client,
        per_topic_limit=per_topic_limit,
        warnings=warnings,
    )
    normalized_records_by_url: dict[str, RepoCatalogRecord] = {}
    for seed_entry in seed_entries:
        if seed_entry.name.lower() in launch_overrides.blocklist:
            continue

        try:
            payload, topic = _resolve_seed_repo_payload(
                seed_entry,
                client=client,
                topic_candidates=topic_candidates,
            )
            record = _normalize_github_repo(
                payload,
                topic=topic,
                bundled_launch_catalog=True,
                metadata_override=launch_overrides.overrides.get(seed_entry.name.lower()),
            )
        except Exception as exc:
            _warn_bundled_repo_catalog_skip(
                f"Warning: {seed_entry.name} failed - {exc} - skipping",
                warnings=warnings,
            )
            continue

        normalized_records_by_url[record.repo_url] = record

    if not normalized_records_by_url:
        detail = f" Warnings: {' | '.join(warnings)}" if warnings else ""
        raise RepoCatalogRefreshError(f"Catalog generation failed - zero repos fetched successfully.{detail}")

    return tuple(sorted(normalized_records_by_url.values(), key=lambda item: (-item.stars, item.name.lower())))


def write_bundled_repo_catalog(records: tuple[RepoCatalogRecord, ...]) -> Path:
    """Write the bundled repo catalog asset after successful generation."""

    bundled_path = resolve_bundled_repo_catalog_path()
    _write_repo_catalog_file(bundled_path, records)
    return bundled_path


def refresh_local_repo_catalog(
    config_dir: Path,
    *,
    client: Any | None = None,
    per_topic_limit: int = DEFAULT_TOPIC_FETCH_LIMIT,
) -> RepoCatalogRefreshResult:
    """Refresh the local repo catalog from GitHub topics, preserving cache on failure."""

    cache_path = initialize_local_repo_catalog_cache(config_dir)
    previous_cache = cache_path.read_text(encoding="utf-8")

    try:
        records = _fetch_repo_catalog_records(client=client, per_topic_limit=per_topic_limit)
        _write_repo_catalog_file(cache_path, records)
    except Exception as exc:
        if not cache_path.exists():
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(previous_cache, encoding="utf-8")
        return RepoCatalogRefreshResult(
            ok=False,
            message=_format_refresh_error(exc),
            records=load_sorted_local_repo_catalog(config_dir),
        )

    refreshed_records = load_sorted_local_repo_catalog(config_dir)
    return RepoCatalogRefreshResult(
        ok=True,
        message=f"Repo catalog refreshed: {len(refreshed_records)} repositories cached.",
        records=refreshed_records,
    )


def _load_repo_catalog_file(path: Path) -> tuple[RepoCatalogRecord, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    repos = _extract_repo_rows(payload, source=str(path))
    return tuple(_parse_repo_catalog_record(item, source=str(path)) for item in repos)


def _fetch_repo_catalog_records(
    *,
    client: Any | None,
    per_topic_limit: int,
) -> tuple[RepoCatalogRecord, ...]:
    if per_topic_limit <= 0:
        raise ValueError("Repo catalog refresh requires a positive per-topic limit.")

    normalized_records_by_url: dict[str, RepoCatalogRecord] = {}
    for topic in GITHUB_REPO_TOPICS:
        for item in _search_github_topic(topic, client=client, per_page=per_topic_limit):
            record = _normalize_github_repo(item, topic=topic)
            existing = normalized_records_by_url.get(record.repo_url)
            if existing is None or (record.stars, record.name.lower()) > (existing.stars, existing.name.lower()):
                normalized_records_by_url[record.repo_url] = record

    return tuple(sorted(normalized_records_by_url.values(), key=lambda item: (-item.stars, item.name.lower())))


def _fetch_topic_candidate_payloads(
    *,
    client: Any | None,
    per_topic_limit: int,
    warnings: list[str] | None = None,
) -> dict[str, tuple[dict[str, Any], str]]:
    candidates: dict[str, tuple[dict[str, Any], str]] = {}
    for topic in GITHUB_REPO_TOPICS:
        try:
            topic_items = _search_github_topic(topic, client=client, per_page=per_topic_limit)
        except Exception as exc:
            _warn_bundled_repo_catalog_skip(
                f"Warning: topic '{topic}' failed - {exc} - skipping",
                warnings=warnings,
            )
            continue

        for item in topic_items:
            repo_url = str(item.get("html_url") or "").strip()
            if not repo_url:
                continue
            candidates.setdefault(repo_url, (item, topic))
    return candidates


def _search_github_topic(topic: str, *, client: Any | None, per_page: int) -> list[dict[str, Any]]:
    response = _github_get(
        "/search/repositories",
        params={
            "q": f"topic:{topic} archived:false is:public",
            "sort": "stars",
            "order": "desc",
            "per_page": str(per_page),
            "page": "1",
        },
        client=client,
    )

    status_code = getattr(response, "status_code", None)
    if status_code != 200:
        raise RepoCatalogRefreshError(_format_github_api_error(response, f"GitHub topic fetch failed for '{topic}'"))

    payload = response.json()
    items = payload.get("items")
    if not isinstance(items, list):
        raise RepoCatalogRefreshError(f"GitHub topic fetch returned an invalid payload for '{topic}'.")
    return [item for item in items if isinstance(item, dict)]


def _resolve_seed_repo_payload(
    seed_entry: LaunchCatalogSeedEntry,
    *,
    client: Any | None,
    topic_candidates: dict[str, tuple[dict[str, Any], str]],
) -> tuple[dict[str, Any], str]:
    cached_candidate = topic_candidates.get(seed_entry.repo_url)

    try:
        payload = _fetch_github_repo_by_url(seed_entry.repo_url, client=client)
    except Exception:
        if cached_candidate is None:
            raise
        payload, topic = cached_candidate
        return _merge_seed_into_github_payload(seed_entry, payload), topic

    topic = cached_candidate[1] if cached_candidate is not None else _infer_topic_from_payload(payload)
    return _merge_seed_into_github_payload(seed_entry, payload), topic


def _merge_seed_into_github_payload(seed_entry: LaunchCatalogSeedEntry, payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload)
    merged.setdefault("name", seed_entry.name)
    merged["html_url"] = seed_entry.repo_url
    return merged


def _fetch_github_repo_by_url(repo_url: str, *, client: Any | None) -> dict[str, Any]:
    owner_and_repo = _extract_github_repo_path(repo_url)
    response = _github_get(f"/repos/{owner_and_repo}", params={}, client=client)
    status_code = getattr(response, "status_code", None)
    if status_code != 200:
        raise RepoCatalogRefreshError(_format_github_api_error(response, f"GitHub repo fetch failed for '{repo_url}'"))

    payload = response.json()
    if not isinstance(payload, dict):
        raise RepoCatalogRefreshError(f"GitHub repo fetch returned an invalid payload for '{repo_url}'.")
    return payload


def _extract_github_repo_path(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "github.com":
        raise ValueError(f"Launch catalog seed repo_url must be a GitHub URL: {repo_url}")

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2:
        raise ValueError(f"Launch catalog seed repo_url is missing owner/repo: {repo_url}")

    owner, repo = path_parts[0], path_parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{owner}/{repo}"


def _infer_topic_from_payload(payload: dict[str, Any]) -> str:
    searchable_text = _repo_searchable_text(payload)
    if any(signal in searchable_text for signal in STABLE_DIFFUSION_CATEGORY_SIGNALS):
        return "stable-diffusion"
    if any(signal in searchable_text for signal in AUDIO_CATEGORY_SIGNALS):
        return "huggingface"
    if any(signal in searchable_text for signal in COMPUTER_VISION_CATEGORY_SIGNALS):
        return "computer-vision"
    if any(signal in searchable_text for signal in LLM_CATEGORY_SIGNALS):
        return "llm"
    return "machine-learning"


def _include_in_bundled_launch_catalog(payload: dict[str, Any], *, launch_overrides: LaunchCatalogOverrides) -> bool:
    if _repo_name_key(payload) in launch_overrides.blocklist:
        return False
    if _is_explicitly_excluded_bundled_repo(payload):
        return False
    if _repo_name_key(payload) in launch_overrides.allowlist:
        return True
    if _is_launch_priority_repo(payload):
        return True
    if _contains_any_signal(payload, TRAINING_SIGNALS):
        return False
    if _contains_any_signal(payload, NON_RUNNABLE_LEARNING_SIGNALS):
        return False
    if not _has_allowed_primary_language(payload):
        return False
    return _contains_any_signal(payload, PRACTICAL_RUNNABLE_SIGNALS)


def _is_launch_priority_repo(payload: dict[str, Any]) -> bool:
    return _repo_name_key(payload) in LAUNCH_PRIORITY_REPO_NAMES


def _is_explicitly_excluded_bundled_repo(payload: dict[str, Any]) -> bool:
    return _repo_name_key(payload) in EXCLUDED_BUNDLED_REPO_NAMES


def _repo_name_key(payload: dict[str, Any]) -> str:
    return str(payload.get("name") or "").strip().lower()


def _has_allowed_primary_language(payload: dict[str, Any]) -> bool:
    language = str(payload.get("language") or "Unknown").strip().lower()
    return language not in EXCLUDED_PRIMARY_LANGUAGES


def _contains_any_signal(payload: dict[str, Any], signals: tuple[str, ...]) -> bool:
    searchable_text = _repo_searchable_text(payload)
    return any(signal.lower() in searchable_text for signal in signals)


def _repo_searchable_text(payload: dict[str, Any]) -> str:
    searchable_parts = [
        str(payload.get("name") or "").lower(),
        str(payload.get("description") or "").lower(),
    ]
    topics = payload.get("topics")
    if isinstance(topics, list):
        searchable_parts.extend(str(topic).lower() for topic in topics)
    return " ".join(searchable_parts)


def _github_get(path: str, *, params: dict[str, str], client: Any | None) -> Any:
    url = f"{GITHUB_API_BASE_URL}{path}"
    headers = _github_request_headers()

    if client is not None:
        return _github_client_get(client, url=url, headers=headers, params=params)

    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required for repo catalog refresh.") from exc

    with httpx.Client(headers=headers, timeout=10.0, follow_redirects=True) as http_client:
        return http_client.get(url, params=params)


def _github_client_get(client: Any, *, url: str, headers: dict[str, str], params: dict[str, str]) -> Any:
    try:
        return client.get(url, headers=headers, params=params, timeout=10.0, follow_redirects=True)
    except TypeError:
        try:
            return client.get(url, headers=headers, params=params, timeout=10.0, allow_redirects=True)
        except TypeError:
            return client.get(url, headers=headers, params=params, timeout=10.0)


def _github_request_headers() -> dict[str, str]:
    headers = {
        "Accept": GITHUB_API_ACCEPT,
        "User-Agent": GITHUB_API_USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _format_github_api_error(response: Any, context: str) -> str:
    status_code = getattr(response, "status_code", None)
    detail = _github_response_detail(response)
    if status_code == 403 and detail:
        return f"{context} (HTTP 403): {detail}"
    return f"{context} (HTTP {status_code})."


def _github_response_detail(response: Any) -> str:
    detail = (getattr(response, "text", "") or "").strip()
    if detail:
        return detail

    try:
        payload = response.json()
    except Exception:
        return ""

    if payload in (None, ""):
        return ""

    try:
        return json.dumps(payload, separators=(",", ":"))
    except TypeError:
        return str(payload).strip()


def _warn_bundled_repo_catalog_skip(message: str, *, warnings: list[str] | None) -> None:
    print(message, file=sys.stderr)
    if warnings is not None:
        warnings.append(message)


def _normalize_github_repo(
    payload: dict[str, Any],
    *,
    topic: str,
    bundled_launch_catalog: bool = False,
    metadata_override: LaunchCatalogMetadataOverride | None = None,
) -> RepoCatalogRecord:
    repo_url = _normalize_repo_field(payload.get("html_url"), field_name="repo_url")
    name = _normalize_repo_field(payload.get("name"), field_name="name")
    stars = _normalize_star_count(payload.get("stargazers_count"), source=repo_url)

    description_value = payload.get("description") or "No description available."
    description = _normalize_repo_field(description_value, field_name="description")

    category_value = _category_for_repo(payload, topic=topic, bundled_launch_catalog=bundled_launch_catalog)
    framework_value = _framework_for_repo(payload, bundled_launch_catalog=bundled_launch_catalog)
    warning_value: str | None = None
    if metadata_override is not None:
        if metadata_override.category is not None:
            category_value = metadata_override.category
        if metadata_override.framework is not None:
            framework_value = metadata_override.framework
        warning_value = metadata_override.warning

    category = _normalize_repo_field(category_value, field_name="category")
    framework = _normalize_repo_field(framework_value, field_name="framework")
    last_updated = _normalize_last_updated(payload.get("updated_at"), source=repo_url)

    return RepoCatalogRecord(
        name=name,
        repo_url=repo_url,
        stars=stars,
        description=description,
        category=category,
        framework=framework,
        last_updated=last_updated,
        warning=_normalize_repo_field(warning_value, field_name="warning") if warning_value is not None else None,
    )


def _category_for_repo(payload: dict[str, Any], *, topic: str, bundled_launch_catalog: bool) -> str:
    if bundled_launch_catalog:
        override = BUNDLED_CATEGORY_OVERRIDES.get(_repo_name_key(payload))
        if override is not None:
            return override
        inferred = _infer_bundled_category(payload, topic=topic)
        if inferred is not None:
            return inferred
    return _category_for_topic(topic)


def _category_for_topic(topic: str) -> str:
    mapping = {
        "machine-learning": "Machine Learning",
        "deep-learning": "Deep Learning",
        "llm": "LLM",
        "stable-diffusion": "Stable Diffusion",
        "computer-vision": "Computer Vision",
        "pytorch": "PyTorch",
        "huggingface": "Hugging Face",
    }
    return mapping.get(topic, topic.replace("-", " ").title())


def _framework_for_repo(payload: dict[str, Any], *, bundled_launch_catalog: bool) -> str:
    if bundled_launch_catalog:
        override = BUNDLED_FRAMEWORK_OVERRIDES.get(_repo_name_key(payload))
        if override is not None:
            return override

    language = payload.get("language")
    topics = payload.get("topics")
    normalized_topics = {str(topic).lower() for topic in topics} if isinstance(topics, list) else set()

    if "pytorch" in normalized_topics:
        return "Python/PyTorch"
    if "huggingface" in normalized_topics or "transformers" in normalized_topics:
        return "Python/Hugging Face"
    if "stable-diffusion" in normalized_topics:
        return "Python/Stable Diffusion"
    if language:
        return str(language)
    return "Unknown"


def _infer_bundled_category(payload: dict[str, Any], *, topic: str) -> str | None:
    searchable_text = _repo_searchable_text(payload)

    if any(signal in searchable_text for signal in STABLE_DIFFUSION_CATEGORY_SIGNALS):
        return "Stable Diffusion"
    if any(signal in searchable_text for signal in AUDIO_CATEGORY_SIGNALS):
        return "Audio"
    if any(signal in searchable_text for signal in COMPUTER_VISION_CATEGORY_SIGNALS):
        return "Computer Vision"
    if any(signal in searchable_text for signal in LLM_CATEGORY_SIGNALS):
        return "LLM"

    if topic == "llm":
        return "LLM"
    if topic == "stable-diffusion":
        return "Stable Diffusion"
    if topic == "computer-vision":
        return "Computer Vision"
    if topic in {"pytorch", "huggingface"}:
        return "Machine Learning"
    return None


def _normalize_last_updated(value: Any, *, source: str) -> str:
    if not value:
        raise ValueError(f"Repo catalog {source} is missing last-updated data.")
    normalized = str(value).strip()
    if "T" in normalized:
        return normalized.split("T", 1)[0]
    return normalized


def _write_repo_catalog_file(path: Path, records: tuple[RepoCatalogRecord, ...]) -> None:
    payload = {
        "last_updated": date.today().isoformat(),
        "repos": [
            {
                "name": record.name,
                "repo_url": record.repo_url,
                "stars": record.stars,
                "description": record.description,
                "category": record.category,
                "framework": record.framework,
                "last_updated": record.last_updated,
                **({"warning": record.warning} if record.warning is not None else {}),
            }
            for record in records
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def _format_refresh_error(exc: Exception) -> str:
    message = str(exc).strip() or "Unknown refresh failure."
    return f"Repo catalog refresh failed: {message}"


def _extract_repo_rows(payload: Any, *, source: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError(f"Repo catalog {source} must be a JSON object.")

    repos = payload.get("repos")
    if not isinstance(repos, list):
        raise ValueError(f"Repo catalog {source} must contain a 'repos' list.")

    for item in repos:
        if not isinstance(item, dict):
            raise ValueError(f"Repo catalog {source} contains a non-object repo entry.")
    return repos


def _parse_repo_catalog_record(payload: dict[str, Any], *, source: str) -> RepoCatalogRecord:
    required_fields = (
        "name",
        "repo_url",
        "stars",
        "description",
        "category",
        "framework",
        "last_updated",
    )
    missing = [field for field in required_fields if field not in payload]
    if missing:
        raise ValueError(f"Repo catalog {source} is missing fields: {', '.join(missing)}")

    return RepoCatalogRecord(
        name=_normalize_repo_field(payload["name"], field_name="name"),
        repo_url=_normalize_repo_field(payload["repo_url"], field_name="repo_url"),
        stars=_normalize_star_count(payload["stars"], source=source),
        description=_normalize_repo_field(payload["description"], field_name="description"),
        category=_normalize_repo_field(payload["category"], field_name="category"),
        framework=_normalize_repo_field(payload["framework"], field_name="framework"),
        last_updated=_normalize_repo_field(payload["last_updated"], field_name="last_updated"),
        warning=_normalize_repo_field(payload["warning"], field_name="warning") if "warning" in payload else None,
    )


def _parse_launch_catalog_overrides(payload: Any) -> LaunchCatalogOverrides:
    if not isinstance(payload, dict):
        raise ValueError("Launch catalog overrides must be a JSON object.")

    allowlist = _parse_repo_name_list(payload.get("allowlist"), field_name="allowlist")
    blocklist = _parse_repo_name_list(payload.get("blocklist"), field_name="blocklist")

    raw_overrides = payload.get("overrides") or {}
    if not isinstance(raw_overrides, dict):
        raise ValueError("Launch catalog overrides must contain an object 'overrides' field.")

    overrides: dict[str, LaunchCatalogMetadataOverride] = {}
    for repo_name, repo_override in raw_overrides.items():
        normalized_name = _normalize_repo_field(repo_name, field_name="override repo name").lower()
        if not isinstance(repo_override, dict):
            raise ValueError(f"Launch catalog override for '{repo_name}' must be an object.")
        overrides[normalized_name] = LaunchCatalogMetadataOverride(
            category=_normalize_repo_field(repo_override["category"], field_name="override category") if "category" in repo_override else None,
            framework=_normalize_repo_field(repo_override["framework"], field_name="override framework") if "framework" in repo_override else None,
            warning=_normalize_repo_field(repo_override["warning"], field_name="override warning") if "warning" in repo_override else None,
        )

    return LaunchCatalogOverrides(
        allowlist=frozenset(allowlist),
        blocklist=frozenset(blocklist),
        overrides=overrides,
    )


def _parse_launch_catalog_seed(payload: Any) -> tuple[LaunchCatalogSeedEntry, ...]:
    if not isinstance(payload, dict):
        raise ValueError("Launch catalog seed must be a JSON object.")

    repos = payload.get("repos")
    if not isinstance(repos, list):
        raise ValueError("Launch catalog seed must contain a 'repos' list.")

    records: list[LaunchCatalogSeedEntry] = []
    for item in repos:
        if not isinstance(item, dict):
            raise ValueError("Launch catalog seed contains a non-object repo entry.")
        records.append(
            LaunchCatalogSeedEntry(
                name=_normalize_repo_field(item.get("name"), field_name="seed name"),
                repo_url=_normalize_repo_field(item.get("repo_url"), field_name="seed repo_url"),
            )
        )
    return tuple(records)


def _parse_repo_name_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"Launch catalog overrides field '{field_name}' must be a list.")
    return tuple(_normalize_repo_field(item, field_name=field_name).lower() for item in value)


def _normalize_repo_field(value: Any, *, field_name: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ValueError(f"Repo catalog field '{field_name}' cannot be empty.")
    return normalized


def _normalize_star_count(value: Any, *, source: str) -> int:
    try:
        stars = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Repo catalog {source} contains a non-integer 'stars' value.") from exc
    if stars < 0:
        raise ValueError(f"Repo catalog {source} contains a negative 'stars' value.")
    return stars
