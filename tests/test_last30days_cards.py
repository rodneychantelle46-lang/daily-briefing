import unittest

from src.publishers.feishu import build_afternoon_card, build_morning_card


LAST30DAYS_ITEM = {
    "title": "Developers compare agent workflows",
    "url": "https://example.com/agents",
    "source": "hackernews",
    "label": "AI Agent 社区热议",
    "summary": "HN discussion is focusing on workflow reliability.",
}


class Last30DaysCardTests(unittest.TestCase):
    def test_morning_card_shows_last30days_block_when_items_exist(self):
        card = build_morning_card(
            general_news=[],
            interest_news={},
            date_str="2026年07月07日",
            last30days_items=[LAST30DAYS_ITEM],
        )

        content = "\n".join(element.get("content", "") for element in card["elements"])
        self.assertIn("AI 情报 / 社区热议", content)
        self.assertIn("Developers compare agent workflows", content)
        self.assertIn("观察：HN discussion is focusing on workflow reliability.", content)

    def test_morning_card_omits_last30days_block_when_items_are_empty(self):
        card = build_morning_card(
            general_news=[],
            interest_news={},
            date_str="2026年07月07日",
            last30days_items=[],
        )

        content = "\n".join(element.get("content", "") for element in card["elements"])
        self.assertNotIn("AI 情报 / 社区热议", content)

    def test_afternoon_card_shows_last30days_block_near_github_section(self):
        card = build_afternoon_card(
            tips=[],
            date_str="2026年07月07日",
            github_repos=[{"name": "owner/repo", "url": "https://github.com/owner/repo"}],
            last30days_items=[LAST30DAYS_ITEM],
        )

        content = "\n".join(element.get("content", "") for element in card["elements"])
        self.assertIn("AI 情报 / 社区热议", content)
        self.assertLess(content.index("AI 情报 / 社区热议"), content.index("GitHub 热门项目"))


if __name__ == "__main__":
    unittest.main()
