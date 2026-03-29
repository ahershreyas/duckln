"""Bundled and cached repo catalog helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import shutil
from typing import Any

from agent.memory import resolve_agent_memory_paths


REPO_CATALOG_FILE_NAME = "repos.json"
GITHUB_API_BASE_URL = "https://api.github.com"
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
        "ultralytics",
        "fastchat",
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


@dataclass(frozen=True)
class RepoCatalogRefreshResult:
    """Result of a manual repo catalog refresh."""

    ok: bool
    message: str
    records: tuple[RepoCatalogRecord, ...]


def resolve_bundled_repo_catalog_path() -> Path:
    """Return the bundled repo catalog asset path."""

    return Path(__file__).resolve().parents[1] / "assets" / REPO_CATALOG_FILE_NAME


def resolve_local_repo_catalog_cache_path(config_dir: Path) -> Path:
    """Return the per-user cached repo catalog path under Duckln knowledge."""

    return resolve_agent_memory_paths(config_dir).knowledge_dir / REPO_CATALOG_FILE_NAME


def load_bundled_repo_catalog() -> tuple[RepoCatalogRecord, ...]:
    """Load repo records from the bundled catalog asset."""

    return _load_repo_catalog_file(resolve_bundled_repo_catalog_path())


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

    normalized_records_by_url: dict[str, RepoCatalogRecord] = {}
    for topic in GITHUB_REPO_TOPICS:
        for item in _search_github_topic(topic, client=client, per_page=per_topic_limit):
            if not _include_in_bundled_launch_catalog(item):
                continue
            record = _normalize_github_repo(item, topic=topic)
            existing = normalized_records_by_url.get(record.repo_url)
            if existing is None or (record.stars, record.name.lower()) > (existing.stars, existing.name.lower()):
                normalized_records_by_url[record.repo_url] = record

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
        raise RepoCatalogRefreshError(f"GitHub topic fetch failed for '{topic}' (HTTP {status_code}).")

    payload = response.json()
    items = payload.get("items")
    if not isinstance(items, list):
        raise RepoCatalogRefreshError(f"GitHub topic fetch returned an invalid payload for '{topic}'.")
    return [item for item in items if isinstance(item, dict)]


def _include_in_bundled_launch_catalog(payload: dict[str, Any]) -> bool:
    if _is_launch_priority_repo(payload):
        return True
    if _contains_any_signal(payload, TRAINING_SIGNALS):
        return False
    return _contains_any_signal(payload, PRACTICAL_RUNNABLE_SIGNALS)


def _is_launch_priority_repo(payload: dict[str, Any]) -> bool:
    name = str(payload.get("name") or "").strip().lower()
    return name in LAUNCH_PRIORITY_REPO_NAMES


def _contains_any_signal(payload: dict[str, Any], signals: tuple[str, ...]) -> bool:
    searchable_parts = [str(payload.get("description") or "").lower()]
    topics = payload.get("topics")
    if isinstance(topics, list):
        searchable_parts.extend(str(topic).lower() for topic in topics)
    searchable_text = " ".join(searchable_parts)
    return any(signal.lower() in searchable_text for signal in signals)


def _github_get(path: str, *, params: dict[str, str], client: Any | None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "duckln-repo-catalog",
    }
    url = f"{GITHUB_API_BASE_URL}{path}"

    if client is not None:
        return client.get(url, headers=headers, params=params, timeout=10.0)

    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required for repo catalog refresh.") from exc

    with httpx.Client() as http_client:
        return http_client.get(url, headers=headers, params=params, timeout=10.0)


def _normalize_github_repo(payload: dict[str, Any], *, topic: str) -> RepoCatalogRecord:
    repo_url = _normalize_repo_field(payload.get("html_url"), field_name="repo_url")
    name = _normalize_repo_field(payload.get("name"), field_name="name")
    stars = _normalize_star_count(payload.get("stargazers_count"), source=repo_url)

    description_value = payload.get("description") or "No description available."
    description = _normalize_repo_field(description_value, field_name="description")

    category = _normalize_repo_field(_category_for_topic(topic), field_name="category")
    framework = _normalize_repo_field(_framework_for_repo(payload), field_name="framework")
    last_updated = _normalize_last_updated(payload.get("updated_at"), source=repo_url)

    return RepoCatalogRecord(
        name=name,
        repo_url=repo_url,
        stars=stars,
        description=description,
        category=category,
        framework=framework,
        last_updated=last_updated,
    )


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


def _framework_for_repo(payload: dict[str, Any]) -> str:
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
    )


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
