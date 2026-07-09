import json
import tempfile
import unittest
from pathlib import Path

from src.fetchers.last30days_fetcher import load_last30days_findings, normalize_last30days_payload


class Last30DaysFetcherTests(unittest.TestCase):
    def test_missing_cache_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "missing.json"

            self.assertEqual(load_last30days_findings(path), [])

    def test_malformed_cache_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.json"
            path.write_text("{not json", encoding="utf-8")

            self.assertEqual(load_last30days_findings(path), [])

    def test_normalizes_ranked_candidates(self):
        payload = {
            "ranked_candidates": [
                {
                    "title": "AI agents are moving into workflows",
                    "url": "https://news.ycombinator.com/item?id=1",
                    "source": "hackernews",
                    "snippet": "Developers are comparing agent workflow patterns.",
                    "final_score": 42,
                    "source_items": [
                        {
                            "published_at": "2026-06-20T00:00:00Z",
                            "engagement": {"points": 120, "comments": 34},
                        }
                    ],
                }
            ]
        }

        items = normalize_last30days_payload(payload, topic="AI agents", label="AI Agent 社区热议")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "AI agents are moving into workflows")
        self.assertEqual(items[0]["source"], "hackernews")
        self.assertEqual(items[0]["label"], "AI Agent 社区热议")
        self.assertEqual(items[0]["summary"], "Developers are comparing agent workflow patterns.")
        self.assertEqual(items[0]["published_at"], "2026-06-20T00:00:00Z")

    def test_falls_back_to_items_by_source(self):
        payload = {
            "items_by_source": {
                "github": [
                    {
                        "title": "owner/repo",
                        "url": "https://github.com/owner/repo",
                        "body": "Repository activity is rising.",
                        "engagement": {"stars": 500},
                    }
                ]
            }
        }

        items = normalize_last30days_payload(payload, topic="OpenAI Codex", label="Codex / 编程智能体")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "github")
        self.assertEqual(items[0]["summary"], "Repository activity is rising.")

    def test_loads_sidecar_items_and_dedupes(self):
        payload = {
            "items": [
                {"title": "First", "url": "https://example.com/a", "source": "reddit"},
                {"title": "First duplicate", "url": "https://example.com/a", "source": "hackernews"},
                {"title": "Second", "url": "https://example.com/b", "source": "github"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "last30days_findings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            items = load_last30days_findings(path, max_items=5)

        self.assertEqual([item["title"] for item in items], ["First", "Second"])


if __name__ == "__main__":
    unittest.main()
