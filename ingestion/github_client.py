"""GitHub REST API client for retrieving repository issues."""

from __future__ import annotations

import os
from typing import Any
import requests


class GitHubAPIError(Exception):
    """Exception raised for GitHub API communication or response errors."""
    pass


class GitHubRateLimitError(GitHubAPIError):
    """Exception raised when GitHub API rate limit is exceeded."""
    pass


class GitHubAuthenticationError(GitHubAPIError):
    """Exception raised for authentication or permission failures."""
    pass


class GitHubClient:
    """Client for making authenticated or unauthenticated calls to GitHub REST API."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = token or os.getenv("GITHUB_TOKEN")

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-issue-agent",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get_issues(
        self,
        repo: str,
        state: str = "all",
        page: int = 1,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch a page of issues for a repository from GitHub REST API.

        Args:
            repo: Target repository in 'owner/repo' format.
            state: Issue state filter ('open', 'closed', 'all').
            page: Page number for pagination (1-indexed).
            per_page: Number of items per page (maximum 100).

        Returns:
            List of raw issue dictionaries returned by the GitHub API.

        Raises:
            GitHubRateLimitError: If API rate limit has been exceeded.
            GitHubAuthenticationError: If credentials or permissions are invalid.
            GitHubAPIError: For other HTTP or connection errors.
        """
        url = f"{self.base_url}/repos/{repo}/issues"
        params = {
            "state": state,
            "page": page,
            "per_page": per_page,
            "sort": "created",
            "direction": "desc",
        }

        try:
            response = requests.get(
                url,
                headers=self._get_headers(),
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as err:
            raise GitHubAPIError(f"Network error connecting to GitHub API: {err}") from err

        if response.status_code == 200:
            return response.json()

        if response.status_code == 401:
            raise GitHubAuthenticationError(
                "GitHub API authentication failed (401). Please verify your GITHUB_TOKEN."
            )

        if response.status_code == 403:
            remaining = response.headers.get("x-ratelimit-remaining")
            message = response.json().get("message", "") if response.content else ""
            if remaining == "0" or "rate limit" in message.lower():
                reset_time = response.headers.get("x-ratelimit-reset", "unknown")
                raise GitHubRateLimitError(
                    f"GitHub API rate limit exceeded (403). Reset at timestamp {reset_time}. "
                    "Set GITHUB_TOKEN environment variable for higher rate limits."
                )
            raise GitHubAuthenticationError(
                f"GitHub API permission denied (403): {message or response.text}"
            )

        if response.status_code == 404:
            raise GitHubAPIError(f"GitHub repository '{repo}' not found (404).")

        raise GitHubAPIError(
            f"GitHub API request failed with status {response.status_code}: {response.text}"
        )
