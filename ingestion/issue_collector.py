"""Issue collector to retrieve, filter, normalize, and store GitHub issues."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
import yaml

from ingestion.github_client import GitHubClient


def extract_label_names(raw_labels: list[Any] | None) -> list[str]:
    """Convert raw labels from GitHub API into a flat list of label names."""
    if not raw_labels:
        return []
    label_names: list[str] = []
    for label in raw_labels:
        if isinstance(label, dict):
            name = label.get("name")
            if name:
                label_names.append(str(name))
        elif isinstance(label, str):
            label_names.append(label)
    return label_names


def is_pull_request(raw_record: dict[str, Any]) -> bool:
    """Check if a GitHub issue record is actually a pull request."""
    return "pull_request" in raw_record


def normalize_issue(raw_record: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw GitHub issue dictionary into the target schema."""
    return {
        "id": raw_record.get("id"),
        "number": raw_record.get("number"),
        "title": raw_record.get("title", ""),
        "body": raw_record.get("body") or "",
        "state": raw_record.get("state", ""),
        "labels": extract_label_names(raw_record.get("labels")),
        "created_at": raw_record.get("created_at"),
        "updated_at": raw_record.get("updated_at"),
        "url": raw_record.get("html_url") or raw_record.get("url", ""),
    }


def load_settings(config_path: str | Path = "config/settings.yaml") -> dict[str, Any]:
    """Load settings from YAML configuration file."""
    path = Path(config_path)
    if not path.is_absolute():
        cwd_path = Path.cwd() / path
        if cwd_path.exists():
            path = cwd_path
        else:
            repo_root_path = Path(__file__).resolve().parent.parent / path
            if repo_root_path.exists():
                path = repo_root_path

    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found at: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        return data or {}


def collect_issues(
    repo: str | None = None,
    limit: int | None = None,
    per_page: int = 100,
    output_path: str | Path | None = None,
    client: GitHubClient | None = None,
    config_path: str | Path = "config/settings.yaml",
) -> dict[str, Any]:
    """Retrieve, filter, normalize, and persist issues from GitHub.

    Args:
        repo: Repository identifier in 'owner/repo' format. If None, loaded from settings.
        limit: Maximum number of issues to collect. If None, loaded from settings (default 100).
        per_page: Number of items to fetch per API page (maximum 100).
        output_path: File path to save normalized JSON issues. Default 'data/issues.json'.
        client: Optional GitHubClient instance for API communication.
        config_path: Path to configuration YAML file.

    Returns:
        Summary dict containing collection statistics.
    """
    settings = load_settings(config_path) if (repo is None or limit is None) else {}

    target_repo = repo or settings.get("repository")
    if not target_repo:
        raise ValueError("Target repository is not specified in settings or arguments.")

    target_limit = limit if limit is not None else settings.get("max_issues", 100)
    target_output_path = Path(output_path or "data/issues.json")

    api_client = client or GitHubClient()

    records_examined = 0
    pull_requests_excluded = 0
    collected_issues: list[dict[str, Any]] = []
    page = 1

    while len(collected_issues) < target_limit:
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
            collected_issues.append(normalized)

            if len(collected_issues) >= target_limit:
                break

        if len(collected_issues) >= target_limit or len(page_items) < per_page:
            break

        page += 1

    target_output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_output_path, "w", encoding="utf-8") as f:
        json.dump(collected_issues, f, indent=2, ensure_ascii=False)

    print("=" * 45)
    print("GitHub Issue Collection Summary")
    print("=" * 45)
    print(f"Repository:               {target_repo}")
    print(f"GitHub records examined:  {records_examined}")
    print(f"Pull requests excluded:   {pull_requests_excluded}")
    print(f"Issues saved:             {len(collected_issues)}")
    print(f"Output path:              {target_output_path}")
    print("=" * 45)

    return {
        "repository": target_repo,
        "records_examined": records_examined,
        "pull_requests_excluded": pull_requests_excluded,
        "issues_saved": len(collected_issues),
        "output_path": str(target_output_path),
        "issues": collected_issues,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments for the issue collector."""
    parser = argparse.ArgumentParser(
        description="Retrieve, filter, normalize, and store GitHub issues."
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
        help="Maximum number of issues to collect (overrides config/settings.yaml).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to output JSON file (default: data/issues.json).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/settings.yaml",
        help="Path to YAML settings file (default: config/settings.yaml).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for collecting GitHub issues."""
    args = parse_args(argv)
    collect_issues(
        repo=args.repo,
        limit=args.limit,
        output_path=args.output,
        config_path=args.config,
    )


if __name__ == "__main__":
    main()
