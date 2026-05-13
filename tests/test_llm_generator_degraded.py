import json
import os
import unittest
from unittest.mock import patch

from src.processors import llm_generator


class LlmGeneratorDegradedTests(unittest.TestCase):
    def test_generate_tip_missing_key_returns_degraded_status(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            tip = llm_generator.generate_tip("psychology", api_key="")

        self.assertEqual(tip["generation_status"], "degraded")
        self.assertEqual(tip["generation_error"], "missing_api_key")
        self.assertIn("生成失败", tip["topic"])

    def test_generate_tip_low_quality_returns_degraded_status(self):
        low_quality = json.dumps({"title": "短", "content": "太短", "try_this": "看一眼", "topic": "锚定效应"}, ensure_ascii=False)
        with patch.object(llm_generator, "chat_completion", return_value=low_quality), \
             patch.object(llm_generator, "_load_history", return_value=[]), \
             patch.object(llm_generator, "_save_history"):
            tip = llm_generator.generate_tip("psychology", api_key="key")

        self.assertEqual(tip["generation_status"], "degraded")
        self.assertEqual(tip["generation_error"], "low_quality_tip")

    def test_summarize_github_repos_requires_deep_read_metadata(self):
        repos = [{"name": "owner/repo", "description": "A tool", "url": "https://github.com/owner/repo"}]
        result = llm_generator.summarize_github_repos(repos, api_key="key")

        self.assertEqual(result[0]["generation_status"], "degraded")
        self.assertEqual(result[0]["generation_error"], "github_deep_read_missing_or_degraded")

    def test_summarize_github_repos_prompt_uses_metadata(self):
        captured = {}

        def fake_chat_completion(messages, **kwargs):
            captured["prompt"] = messages[0]["content"]
            return json.dumps([
                {
                    "index": 1,
                    "summary": "这是一个基于README的项目摘要，说明用途和边界。",
                    "why": "README与topics能支撑判断",
                    "use_case": "适合团队评估后借鉴自动化流程",
                    "risk": "open issues 偏多，需要先验证维护质量",
                }
            ], ensure_ascii=False)

        repos = [{
            "name": "owner/repo",
            "url": "https://github.com/owner/repo",
            "description": "Automation agent",
            "language": "Python",
            "stars_today": "123 stars today",
            "stars": 1000,
            "forks": 42,
            "license": "MIT",
            "topics": ["agent", "automation"],
            "updated_at": "2026-05-01T00:00:00Z",
            "open_issues_count": 17,
            "readme_excerpt": "README says this project automates browser workflows.",
            "deep_read_status": "ok",
            "deep_read_error": "",
        }]

        with patch.object(llm_generator, "chat_completion", side_effect=fake_chat_completion):
            result = llm_generator.summarize_github_repos(repos, api_key="key")

        self.assertEqual(result[0]["generation_status"], "ok")
        self.assertIn("README 摘要", captured["prompt"])
        self.assertIn("MIT", captured["prompt"])
        self.assertIn("open issues", captured["prompt"])
        self.assertIn("risk", result[0])


if __name__ == "__main__":
    unittest.main()
