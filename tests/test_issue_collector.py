"""Tests for issue collector, normalization, and filtering logic."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ingestion.github_client import GitHubAPIError
from ingestion.issue_collector import (
    collect_issues,
    extract_label_names,
    is_pull_request,
    main,
    normalize_issue,
)


class TestIssueCollector(unittest.TestCase):
    """Test suite for issue collection, normalization, and filtering."""

    def test_normalize_normal_issue(self) -> None:
        raw_issue = {
            "id": 987654321,
            "number": 42,
            "title": "Fix memory leak in extension host",
            "body": "Detailed report of the memory leak.",
            "state": "open",
            "labels": [
                {"id": 1, "name": "bug"},
                {"id": 2, "name": "perf"},
            ],
            "created_at": "2026-01-01T10:00:00Z",
            "updated_at": "2026-01-02T15:30:00Z",
            "html_url": "https://github.com/microsoft/vscode/issues/42",
        }

        normalized = normalize_issue(raw_issue)

        self.assertEqual(
            normalized,
            {
                "id": 987654321,
                "number": 42,
                "title": "Fix memory leak in extension host",
                "body": "Detailed report of the memory leak.",
                "state": "open",
                "labels": ["bug", "perf"],
                "created_at": "2026-01-01T10:00:00Z",
                "updated_at": "2026-01-02T15:30:00Z",
                "url": "https://github.com/microsoft/vscode/issues/42",
            },
        )

    def test_normalize_null_issue_body(self) -> None:
        raw_issue = {
            "id": 12345,
            "number": 10,
            "title": "Issue without body",
            "body": None,
            "state": "closed",
            "labels": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "html_url": "https://github.com/microsoft/vscode/issues/10",
        }

        normalized = normalize_issue(raw_issue)
        self.assertEqual(normalized["body"], "")

    def test_extract_labels_formats(self) -> None:
        # Standard dict labels
        dict_labels = [{"name": "bug"}, {"name": "help wanted"}]
        self.assertEqual(extract_label_names(dict_labels), ["bug", "help wanted"])

        # String labels
        str_labels = ["enhancement", "good first issue"]
        self.assertEqual(extract_label_names(str_labels), ["enhancement", "good first issue"])

        # None or empty
        self.assertEqual(extract_label_names([]), [])
        self.assertEqual(extract_label_names(None), [])

    def test_exclude_pull_requests(self) -> None:
        issue_record = {
            "id": 1,
            "number": 101,
            "title": "Real issue report",
        }
        pr_record = {
            "id": 2,
            "number": 102,
            "title": "PR submission",
            "pull_request": {"url": "https://api.github.com/repos/microsoft/vscode/pulls/102"},
        }

        self.assertFalse(is_pull_request(issue_record))
        self.assertTrue(is_pull_request(pr_record))

    def test_collect_issues_with_pagination_and_pr_filtering(self) -> None:
        mock_client = MagicMock()

        # Page 1 contains 2 issues and 1 PR
        page_1 = [
            {"id": 1, "number": 1, "title": "Issue 1", "body": "B1", "state": "open", "labels": [], "html_url": "u1"},
            {"id": 2, "number": 2, "title": "PR 1", "body": "PR", "pull_request": {}},
            {"id": 3, "number": 3, "title": "Issue 2", "body": "B2", "state": "open", "labels": [], "html_url": "u2"},
        ]
        # Page 2 contains 2 issues
        page_2 = [
            {"id": 4, "number": 4, "title": "Issue 3", "body": "B3", "state": "closed", "labels": [], "html_url": "u3"},
            {"id": 5, "number": 5, "title": "Issue 4", "body": "B4", "state": "open", "labels": [], "html_url": "u4"},
        ]

        mock_client.get_issues.side_effect = [page_1, page_2, []]

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "issues.json"

            # Request limit of 3 issues with per_page=3 to test pagination transition
            summary = collect_issues(
                repo="microsoft/vscode",
                limit=3,
                per_page=3,
                output_path=out_file,
                client=mock_client,
            )

            self.assertEqual(summary["records_examined"], 4)  # 3 from page 1 + 1 from page 2
            self.assertEqual(summary["pull_requests_excluded"], 1)
            self.assertEqual(summary["issues_saved"], 3)
            self.assertTrue(out_file.exists())

            with open(out_file, "r", encoding="utf-8") as f:
                saved = json.load(f)

            self.assertEqual(len(saved), 3)
            self.assertEqual([item["number"] for item in saved], [1, 3, 4])

    def test_collect_issues_handles_client_failure(self) -> None:
        mock_client = MagicMock()
        mock_client.get_issues.side_effect = GitHubAPIError("Rate limit exceeded")

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "issues.json"
            with self.assertRaises(GitHubAPIError):
                collect_issues(
                    repo="microsoft/vscode",
                    limit=10,
                    output_path=out_file,
                    client=mock_client,
                )

    @patch("ingestion.issue_collector.collect_issues")
    def test_cli_help_does_not_trigger_collection(self, mock_collect: MagicMock) -> None:
        """Verify that passing --help displays usage and does not trigger issue collection."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with self.assertRaises(SystemExit) as ctx:
                main(["--help"])

        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("usage:", mock_stdout.getvalue().lower())
        self.assertIn("--repo", mock_stdout.getvalue())
        self.assertIn("--limit", mock_stdout.getvalue())
        mock_collect.assert_not_called()

    @patch("ingestion.issue_collector.collect_issues")
    def test_cli_normal_execution_triggers_collection(self, mock_collect: MagicMock) -> None:
        """Verify that CLI without arguments calls collect_issues with default None values."""
        main([])
        mock_collect.assert_called_once_with(
            repo=None,
            limit=None,
            output_path=None,
            config_path="config/settings.yaml",
        )

    @patch("ingestion.issue_collector.collect_issues")
    def test_cli_custom_flags_passed_to_collection(self, mock_collect: MagicMock) -> None:
        """Verify that CLI flags are properly passed to collect_issues."""
        main([
            "--repo", "octocat/Hello-World",
            "--limit", "25",
            "--output", "data/custom.json",
            "--config", "custom_settings.yaml",
        ])
        mock_collect.assert_called_once_with(
            repo="octocat/Hello-World",
            limit=25,
            output_path="data/custom.json",
            config_path="custom_settings.yaml",
        )


if __name__ == "__main__":
    unittest.main()
