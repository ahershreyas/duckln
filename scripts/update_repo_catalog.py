"""Generate the bundled launch-ready Duckln repo catalog."""

from __future__ import annotations

import sys

from state.repo_catalog import DEFAULT_TOPIC_FETCH_LIMIT, generate_bundled_repo_catalog, write_bundled_repo_catalog


def main() -> int:
    try:
        records = generate_bundled_repo_catalog(per_topic_limit=DEFAULT_TOPIC_FETCH_LIMIT)
        output_path = write_bundled_repo_catalog(records)
    except Exception as exc:
        print(f"Repo catalog generation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Bundled repo catalog updated: {len(records)} repositories written to {output_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
