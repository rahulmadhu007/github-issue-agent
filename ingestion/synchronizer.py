"""Incremental issue synchronizer for GitHub issues."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
import yaml

from ingestion.github_client import GitHubClient
from ingestion.issue_collector import is_pull_request, load_settings, normalize_issue


class SyncStateError(Exception):
    """Raised when synchronization state or local datasets are corrupt or invalid."""
    pass


def compute_content_hash(issue: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash representing semantic content.

    Only fields relevant to semantic indexing (title and body) are included.
    Metadata-only modifications (labels, state, updated_at timestamps) do not
    alter the semantic content hash.
    """
    title = (issue.get("title") or "").strip()
    body = (issue.get("body") or "").strip()
    content = f"{title}\n\n{body}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_sync_state(path: str | Path) -> dict[str, Any]:
    """Load and validate synchronization state from disk.

    Raises:
        SyncStateError: If the file exists but contains invalid or corrupt JSON/schema.
    """
    file_path = Path(path)
    if not file_path.exists():
        return {"repository": None, "last_sync_time": None, "issues": {}}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as err:
        raise SyncStateError(f"Corrupt sync state file at '{file_path}': {err}") from err

    if not isinstance(data, dict) or "issues" not in data or not isinstance(data["issues"], dict):
        raise SyncStateError(f"Invalid sync state schema at '{file_path}'. Expected dictionary with 'issues' mapping.")

    return data


def save_sync_state(path: str | Path, state: dict[str, Any]) -> None:
    """Save synchronization state to disk with atomic safety."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load_local_issues(path: str | Path) -> list[dict[str, Any]]:
    """Load and validate existing local issues dataset from disk.

    Raises:
        SyncStateError: If the file exists but contains invalid or corrupt JSON.
    """
    file_path = Path(path)
    if not file_path.exists():
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as err:
        raise SyncStateError(f"Corrupt local issues dataset at '{file_path}': {err}") from err

    if not isinstance(data, list):
        raise SyncStateError(f"Invalid local issues schema at '{file_path}'. Expected JSON list of issue objects.")

    return data


def save_local_issues(path: str | Path, issues: list[dict[str, Any]]) -> None:
    """Save local issues dataset to disk sorted deterministically by issue number."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    # Sort deterministically by issue number descending
    sorted_issues = sorted(issues, key=lambda item: item.get("number") or 0, reverse=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(sorted_issues, f, indent=2, ensure_ascii=False)


def classify_issue(
    issue: dict[str, Any],
    existing_sync_issues: dict[str, Any],
) -> tuple[str, str]:
    """Classify a normalized issue as NEW, CHANGED, or UNCHANGED.

    Returns:
        tuple of (classification, current_content_hash)
    """
    issue_id = str(issue["id"])
    content_hash = compute_content_hash(issue)

    if issue_id not in existing_sync_issues:
        return "NEW", content_hash

    prev_record = existing_sync_issues[issue_id]
    prev_hash = prev_record.get("content_hash")

    if prev_hash != content_hash:
        return "CHANGED", content_hash

    return "UNCHANGED", content_hash


def synchronize_issues(
    repo: str | None = None,
    limit: int | None = None,
    per_page: int = 100,
    issues_path: str | Path = "data/issues.json",
    sync_state_path: str | Path = "data/sync_state.json",
    client: GitHubClient | None = None,
    config_path: str | Path = "config/settings.yaml",
) -> dict[str, Any]:
    """Fetch issues from GitHub, perform incremental synchronization, and update local state."""
    settings = load_settings(config_path) if (repo is None or limit is None) else {}

    target_repo = repo or settings.get("repository")
    if not target_repo:
        raise ValueError("Target repository is not specified in settings or arguments.")

    target_limit = limit if limit is not None else settings.get("max_issues", 100)
    target_issues_path = Path(issues_path)
    target_sync_path = Path(sync_state_path)

    sync_state = load_sync_state(target_sync_path)
    existing_sync_issues = sync_state.get("issues", {})

    local_issues = load_local_issues(target_issues_path)
    local_issues_map = {item["id"]: item for item in local_issues if "id" in item}

    api_client = client or GitHubClient()

    records_examined = 0
    pull_requests_excluded = 0
    fetched_count = 0
    new_count = 0
    changed_count = 0
    unchanged_count = 0

    page = 1
    new_or_updated_sync_entries: dict[str, Any] = {}

    while fetched_count < target_limit:
        page_items = api_client.get_issues(
            repo=target_repo,
            state="all",
            page=page,
            per_page=per_page,
        )

        if not page_items:
            break

        for item in page_items:
            records_examined += 1

            if is_pull_request(item):
                pull_requests_excluded += 1
                continue

            normalized = normalize_issue(item)
            fetched_count += 1

            classification, content_hash = classify_issue(normalized, existing_sync_issues)

            issue_id = str(normalized["id"])
            new_or_updated_sync_entries[issue_id] = {
                "number": normalized.get("number"),
                "updated_at": normalized.get("updated_at"),
                "content_hash": content_hash,
            }

            if classification == "NEW":
                new_count += 1
                local_issues_map[normalized["id"]] = normalized
            elif classification == "CHANGED":
                changed_count += 1
                local_issues_map[normalized["id"]] = normalized
            else:
                unchanged_count += 1

            if fetched_count >= target_limit:
                break

        if fetched_count >= target_limit or len(page_items) < per_page:
            break

        page += 1

    # Update sync state metadata and entries
    sync_state["repository"] = target_repo
    sync_state["last_sync_time"] = datetime.now(timezone.utc).isoformat()
    existing_sync_issues.update(new_or_updated_sync_entries)
    sync_state["issues"] = existing_sync_issues

    save_sync_state(target_sync_path, sync_state)

    # Save local issues if new/changed issues were observed, or if file was missing
    if new_count > 0 or changed_count > 0 or not target_issues_path.exists():
        save_local_issues(target_issues_path, list(local_issues_map.values()))

    total_local_issues = len(local_issues_map)

    summary = {
        "repository": target_repo,
        "records_examined": records_examined,
        "pull_requests_excluded": pull_requests_excluded,
        "fetched": fetched_count,
        "new": new_count,
        "changed": changed_count,
        "unchanged": unchanged_count,
        "total_local_issues": total_local_issues,
        "sync_state_path": str(target_sync_path),
        "issues_path": str(target_issues_path),
    }

    print("=" * 45)
    print("GitHub Issue Synchronization Summary")
    print("=" * 45)
    print(f"Repository:          {summary['repository']}")
    print(f"Fetched:             {summary['fetched']}")
    print(f"New:                 {summary['new']}")
    print(f"Changed:             {summary['changed']}")
    print(f"Unchanged:           {summary['unchanged']}")
    print(f"Total local issues:  {summary['total_local_issues']}")
    print(f"Sync state:          {summary['sync_state_path']}")
    print("=" * 45)

    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for issue synchronization."""
    parser = argparse.ArgumentParser(
        description="Incrementally synchronize GitHub issues with local state and content hashing."
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Target GitHub repository in 'owner/repo' format (overrides config/settings.yaml).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of issues to fetch for synchronization (overrides config/settings.yaml).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/issues.json",
        help="Path to issues JSON dataset file (default: data/issues.json).",
    )
    parser.add_argument(
        "--sync-state",
        type=str,
        default="data/sync_state.json",
        help="Path to sync state JSON file (default: data/sync_state.json).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/settings.yaml",
        help="Path to YAML settings file (default: config/settings.yaml).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for incremental issue synchronization."""
    args = parse_args(argv)
    synchronize_issues(
        repo=args.repo,
        limit=args.limit,
        issues_path=args.output,
        sync_state_path=args.sync_state,
        config_path=args.config,
    )


if __name__ == "__main__":
    main()
