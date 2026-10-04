"""Tests for GitHubClient HTTP interactions and error handling."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
import requests

from ingestion.github_client import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubRateLimitError,
)


class TestGitHubClient(unittest.TestCase):
    """Test suite for GitHubClient API interactions."""

    def test_headers_with_token(self) -> None:
        client = GitHubClient(token="test-token-123")
        headers = client._get_headers()
        self.assertEqual(headers["Authorization"], "Bearer test-token-123")
        self.assertEqual(headers["Accept"], "application/vnd.github+json")
        self.assertEqual(headers["User-Agent"], "github-issue-agent")

    def test_headers_without_token(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            client = GitHubClient(token=None)
            headers = client._get_headers()
            self.assertNotIn("Authorization", headers)
            self.assertEqual(headers["Accept"], "application/vnd.github+json")

    @patch("ingestion.github_client.requests.get")
    def test_get_issues_success(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"id": 101, "number": 1, "title": "First issue"}
        ]
        mock_get.return_value = mock_response

        client = GitHubClient(token="fake-token")
        result = client.get_issues(repo="microsoft/vscode", page=2, per_page=50)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 101)
        mock_get.assert_called_once_with(
            "https://api.github.com/repos/microsoft/vscode/issues",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "github-issue-agent",
                "Authorization": "Bearer fake-token",
            },
            params={
                "state": "all",
                "page": 2,
                "per_page": 50,
                "sort": "created",
                "direction": "desc",
            },
            timeout=30,
        )

    @patch("ingestion.github_client.requests.get")
    def test_get_issues_401_authentication_error(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Bad credentials"
        mock_get.return_value = mock_response

        client = GitHubClient(token="invalid-token")
        with self.assertRaises(GitHubAuthenticationError) as ctx:
            client.get_issues(repo="microsoft/vscode")
        self.assertIn("authentication failed (401)", str(ctx.exception))

    @patch("ingestion.github_client.requests.get")
    def test_get_issues_403_rate_limit_error(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": "1700000000",
        }
        mock_response.json.return_value = {"message": "API rate limit exceeded"}
        mock_get.return_value = mock_response

        client = GitHubClient()
        with self.assertRaises(GitHubRateLimitError) as ctx:
            client.get_issues(repo="microsoft/vscode")
        self.assertIn("rate limit exceeded", str(ctx.exception).lower())

    @patch("ingestion.github_client.requests.get")
    def test_get_issues_404_not_found(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_get.return_value = mock_response

        client = GitHubClient()
        with self.assertRaises(GitHubAPIError) as ctx:
            client.get_issues(repo="nonexistent/repo")
        self.assertIn("not found (404)", str(ctx.exception))

    @patch("ingestion.github_client.requests.get")
    def test_get_issues_network_timeout(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.Timeout("Connection timed out")

        client = GitHubClient()
        with self.assertRaises(GitHubAPIError) as ctx:
            client.get_issues(repo="microsoft/vscode")
        self.assertIn("Network error connecting to GitHub API", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
