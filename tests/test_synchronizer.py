"""Tests for incremental issue synchronization, content hashing, and sync state."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ingestion.synchronizer import (
    SyncStateError,
    classify_issue,
    compute_content_hash,
    main,
    synchronize_issues,
)


class TestSynchronizer(unittest.TestCase):
    """Test suite for incremental synchronization and state tracking."""

    def setUp(self) -> None:
        self.issue_1 = {
            "id": 1001,
            "number": 1,
            "title": "Fix crash on startup",
            "body": "Application crashes when config file is missing.",
            "state": "open",
            "labels": [{"name": "bug"}],
            "created_at": "2026-01-01T12:00:00Z",
            "updated_at": "2026-01-01T12:00:00Z",
            "html_url": "https://github.com/microsoft/vscode/issues/1",
        }
        self.issue_2 = {
            "id": 1002,
            "number": 2,
            "title": "Add dark mode support",
            "body": "Please add high-contrast dark mode.",
            "state": "open",
            "labels": [{"name": "enhancement"}],
            "created_at": "2026-01-02T12:00:00Z",
            "updated_at": "2026-01-02T12:00:00Z",
            "html_url": "https://github.com/microsoft/vscode/issues/2",
        }

    def test_content_hashing_deterministic(self) -> None:
        """Requirement 8: Verify content hashing is deterministic across multiple calls."""
        issue = {
            "title": "Syntax highlighting error in Python",
            "body": "Indent error occurs on line 10.",
        }
        hash_1 = compute_content_hash(issue)
        hash_2 = compute_content_hash(issue)
        self.assertEqual(hash_1, hash_2)
        self.assertEqual(len(hash_1), 64)  # Valid SHA-256 hex string

        # Different content must yield a different hash
        modified_issue = {
            "title": "Syntax highlighting error in Python",
            "body": "Indent error occurs on line 11.",
        }
        self.assertNotEqual(hash_1, compute_content_hash(modified_issue))

    def test_empty_null_body_handled_safely(self) -> None:
        """Requirement 9: Empty or null issue body does not crash hashing or classification."""
        issue_none = {"title": "Issue without body", "body": None}
        issue_empty = {"title": "Issue without body", "body": ""}

        hash_none = compute_content_hash(issue_none)
        hash_empty = compute_content_hash(issue_empty)

        self.assertEqual(hash_none, hash_empty)
        self.assertIsInstance(hash_none, str)

    def test_first_sync_classifies_all_as_new(self) -> None:
        """Requirement 1: First sync against empty state classifies all issues as NEW."""
        mock_client = MagicMock()
        mock_client.get_issues.return_value = [self.issue_1, self.issue_2]

        with tempfile.TemporaryDirectory() as tmp_dir:
            issues_file = Path(tmp_dir) / "issues.json"
            sync_state_file = Path(tmp_dir) / "sync_state.json"

            summary = synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            self.assertEqual(summary["new"], 2)
            self.assertEqual(summary["changed"], 0)
            self.assertEqual(summary["unchanged"], 0)
            self.assertEqual(summary["total_local_issues"], 2)

            # Check that files were created and contain both issues
            with open(issues_file, "r", encoding="utf-8") as f:
                saved_issues = json.load(f)
            self.assertEqual(len(saved_issues), 2)

            with open(sync_state_file, "r", encoding="utf-8") as f:
                saved_state = json.load(f)
            self.assertIn("1001", saved_state["issues"])
            self.assertIn("1002", saved_state["issues"])

    def test_second_sync_identical_classifies_as_unchanged(self) -> None:
        """Requirement 2: Second sync with identical content classifies all issues as UNCHANGED."""
        mock_client = MagicMock()
        mock_client.get_issues.return_value = [self.issue_1, self.issue_2]

        with tempfile.TemporaryDirectory() as tmp_dir:
            issues_file = Path(tmp_dir) / "issues.json"
            sync_state_file = Path(tmp_dir) / "sync_state.json"

            # Run 1: initial ingestion
            synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            # Run 2: identical issues
            summary_2 = synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            self.assertEqual(summary_2["new"], 0)
            self.assertEqual(summary_2["changed"], 0)
            self.assertEqual(summary_2["unchanged"], 2)
            self.assertEqual(summary_2["total_local_issues"], 2)

    def test_modified_title_causes_changed(self) -> None:
        """Requirement 3: An altered title changes the content hash and classifies as CHANGED."""
        mock_client = MagicMock()
        mock_client.get_issues.return_value = [self.issue_1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            issues_file = Path(tmp_dir) / "issues.json"
            sync_state_file = Path(tmp_dir) / "sync_state.json"

            # Run 1
            synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            # Modify title
            modified_issue = dict(self.issue_1)
            modified_issue["title"] = "Fix crash on startup when settings.json is corrupted"
            mock_client.get_issues.return_value = [modified_issue]

            # Run 2
            summary_2 = synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            self.assertEqual(summary_2["new"], 0)
            self.assertEqual(summary_2["changed"], 1)
            self.assertEqual(summary_2["unchanged"], 0)

            # Verify local dataset has updated title and no duplicates
            with open(issues_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["title"], "Fix crash on startup when settings.json is corrupted")

    def test_modified_body_causes_changed(self) -> None:
        """Requirement 4: An altered body changes the content hash and classifies as CHANGED."""
        mock_client = MagicMock()
        mock_client.get_issues.return_value = [self.issue_1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            issues_file = Path(tmp_dir) / "issues.json"
            sync_state_file = Path(tmp_dir) / "sync_state.json"

            synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            # Modify body
            modified_issue = dict(self.issue_1)
            modified_issue["body"] = "Updated body with more trace logs."
            mock_client.get_issues.return_value = [modified_issue]

            summary_2 = synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            self.assertEqual(summary_2["new"], 0)
            self.assertEqual(summary_2["changed"], 1)
            self.assertEqual(summary_2["unchanged"], 0)

            with open(issues_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved[0]["body"], "Updated body with more trace logs.")

    def test_only_updated_at_changed_does_not_cause_changed(self) -> None:
        """Requirement 5: Metadata-only update with same title/body remains UNCHANGED."""
        mock_client = MagicMock()
        mock_client.get_issues.return_value = [self.issue_1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            issues_file = Path(tmp_dir) / "issues.json"
            sync_state_file = Path(tmp_dir) / "sync_state.json"

            synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            # Modify only updated_at timestamp (e.g. GitHub reaction or internal event)
            timestamp_only_issue = dict(self.issue_1)
            timestamp_only_issue["updated_at"] = "2026-05-10T10:00:00Z"
            mock_client.get_issues.return_value = [timestamp_only_issue]

            summary_2 = synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            self.assertEqual(summary_2["new"], 0)
            self.assertEqual(summary_2["changed"], 0)
            self.assertEqual(summary_2["unchanged"], 1)

            # Check that sync_state updated the updated_at timestamp without semantic change
            with open(sync_state_file, "r", encoding="utf-8") as f:
                saved_state = json.load(f)
            self.assertEqual(saved_state["issues"]["1001"]["updated_at"], "2026-05-10T10:00:00Z")

    def test_new_issue_added_without_duplicating_existing(self) -> None:
        """Requirement 6: New issue is appended and existing issues are not duplicated."""
        mock_client = MagicMock()
        mock_client.get_issues.return_value = [self.issue_1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            issues_file = Path(tmp_dir) / "issues.json"
            sync_state_file = Path(tmp_dir) / "sync_state.json"

            # Sync issue 1
            synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            # Now sync both issue 1 and issue 2
            mock_client.get_issues.return_value = [self.issue_1, self.issue_2]
            summary_2 = synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            self.assertEqual(summary_2["new"], 1)
            self.assertEqual(summary_2["unchanged"], 1)
            self.assertEqual(summary_2["total_local_issues"], 2)

            with open(issues_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved), 2)
            saved_ids = [item["id"] for item in saved]
            self.assertEqual(set(saved_ids), {1001, 1002})

    def test_changed_issue_updates_local_dataset_without_duplicate(self) -> None:
        """Requirement 7: Changed issue updates in-place and total local count does not inflate."""
        mock_client = MagicMock()
        mock_client.get_issues.return_value = [self.issue_1, self.issue_2]

        with tempfile.TemporaryDirectory() as tmp_dir:
            issues_file = Path(tmp_dir) / "issues.json"
            sync_state_file = Path(tmp_dir) / "sync_state.json"

            synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            # Issue 1 is modified, Issue 2 is unchanged
            modified_issue_1 = dict(self.issue_1)
            modified_issue_1["title"] = "Refactored crash handling"
            mock_client.get_issues.return_value = [modified_issue_1, self.issue_2]

            summary_2 = synchronize_issues(
                repo="microsoft/vscode",
                limit=10,
                issues_path=issues_file,
                sync_state_path=sync_state_file,
                client=mock_client,
            )

            self.assertEqual(summary_2["new"], 0)
            self.assertEqual(summary_2["changed"], 1)
            self.assertEqual(summary_2["unchanged"], 1)
            self.assertEqual(summary_2["total_local_issues"], 2)

            with open(issues_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved), 2)
            id_counts = {item["id"]: saved.count(item) for item in saved}
            self.assertTrue(all(count == 1 for count in id_counts.values()))

    def test_corrupt_sync_state_fails_predictably(self) -> None:
        """Requirement 10: Corrupt sync state fails with SyncStateError rather than silent overwrite."""
        mock_client = MagicMock()
        mock_client.get_issues.return_value = [self.issue_1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            issues_file = Path(tmp_dir) / "issues.json"
            sync_state_file = Path(tmp_dir) / "sync_state.json"

            # Corrupt the sync state file
            with open(sync_state_file, "w", encoding="utf-8") as f:
                f.write("{ invalid json")

            with self.assertRaises(SyncStateError):
                synchronize_issues(
                    repo="microsoft/vscode",
                    limit=10,
                    issues_path=issues_file,
                    sync_state_path=sync_state_file,
                    client=mock_client,
                )

    def test_corrupt_local_issues_fails_predictably(self) -> None:
        """Requirement 10: Corrupt local issues dataset fails with SyncStateError."""
        mock_client = MagicMock()
        mock_client.get_issues.return_value = [self.issue_1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            issues_file = Path(tmp_dir) / "issues.json"
            sync_state_file = Path(tmp_dir) / "sync_state.json"

            with open(issues_file, "w", encoding="utf-8") as f:
                f.write("not valid json list")

            with self.assertRaises(SyncStateError):
                synchronize_issues(
                    repo="microsoft/vscode",
                    limit=10,
                    issues_path=issues_file,
                    sync_state_path=sync_state_file,
                    client=mock_client,
                )

    @patch("ingestion.synchronizer.synchronize_issues")
    def test_cli_help_does_not_trigger_synchronization(self, mock_sync: MagicMock) -> None:
        """Requirement 11: CLI --help displays usage and does not trigger synchronization."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with self.assertRaises(SystemExit) as ctx:
                main(["--help"])

        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("usage:", mock_stdout.getvalue().lower())
        self.assertIn("--sync-state", mock_stdout.getvalue())
        mock_sync.assert_not_called()

    @patch("ingestion.synchronizer.synchronize_issues")
    def test_cli_normal_invocation(self, mock_sync: MagicMock) -> None:
        """CLI without arguments passes default None/defaults to synchronize_issues."""
        main([])
        mock_sync.assert_called_once_with(
            repo=None,
            limit=None,
            issues_path="data/issues.json",
            sync_state_path="data/sync_state.json",
            config_path="config/settings.yaml",
        )


if __name__ == "__main__":
    unittest.main()
