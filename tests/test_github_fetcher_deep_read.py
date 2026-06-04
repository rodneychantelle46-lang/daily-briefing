import unittest
from unittest.mock import patch

import requests
from src.fetchers import github_fetcher


class FakeResponse:
    def __init__(self, data=None, text="", status_code=200):
        self._data = data or {}
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"http {self.status_code}")

    def json(self):
        return self._data


class GithubFetcherDeepReadTests(unittest.TestCase):
    def test_enrich_repo_metadata_adds_readme_license_topics(self):
        repo_api = FakeResponse(data={
            "description": "A browser automation agent",
            "license": {"spdx_id": "MIT"},
            "topics": ["agent", "browser"],
            "updated_at": "2026-05-01T00:00:00Z",
            "open_issues_count": 12,
            "stargazers_count": 3456,
            "forks_count": 123,
        })
        readme = FakeResponse(text="# Project\nThis README explains how the agent works.")

        with patch.object(github_fetcher.requests, "get", side_effect=[repo_api, readme]):
            repo = github_fetcher.enrich_repo_metadata({"name": "owner/repo", "url": "https://github.com/owner/repo"})

        self.assertEqual(repo["deep_read_status"], "ok")
        self.assertEqual(repo["license"], "MIT")
        self.assertEqual(repo["topics"], ["agent", "browser"])
        self.assertEqual(repo["open_issues_count"], 12)
        self.assertIn("README explains", repo["readme_excerpt"])

    def test_enrich_repo_metadata_marks_network_failure_degraded(self):
        with patch.object(github_fetcher.requests, "get", side_effect=requests.ConnectionError("offline")):
            repo = github_fetcher.enrich_repo_metadata({"name": "owner/repo"})

        self.assertEqual(repo["deep_read_status"], "degraded")
        self.assertIn("github_deep_read_failed", repo["deep_read_error"])

    def test_github_token_is_sent_to_api_requests(self):
        repo_api = FakeResponse(data={
            "description": "A browser automation agent",
            "license": {"spdx_id": "MIT"},
            "topics": ["agent"],
            "updated_at": "2026-05-01T00:00:00Z",
            "open_issues_count": 1,
            "stargazers_count": 2,
            "forks_count": 3,
        })
        readme = FakeResponse(text="# Project")
        calls = []

        def fake_get(url, headers=None, timeout=None):
            calls.append({"url": url, "headers": headers or {}})
            return repo_api if len(calls) == 1 else readme

        with patch.dict("os.environ", {"GITHUB_TOKEN": "token-123"}), \
             patch.object(github_fetcher.requests, "get", side_effect=fake_get):
            repo = github_fetcher.enrich_repo_metadata({"name": "owner/repo"})

        self.assertEqual(repo["deep_read_status"], "ok")
        self.assertTrue(calls)
        self.assertEqual(calls[0]["headers"].get("Authorization"), "Bearer token-123")


if __name__ == "__main__":
    unittest.main()
