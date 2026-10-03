"""Toutiao public articles: a bounded reader, with no automatic live probe."""

from .base import Channel


def format_article(article: dict) -> str:
    """Render verified article content with provenance and extraction caveats."""
    lines = [f"# {article['title']}", ""]
    if article.get("source"):
        lines.append(f"来源：{article['source']}")
    if article.get("published_at"):
        lines.append(f"发布时间：{article['published_at']}")
    lines.extend([
        f"原文：{article['url']}",
        f"获取时间：{article['retrieved_at']}",
        f"读取方式：{article['backend']}",
        "",
        article["content"],
    ])
    if article.get("warnings"):
        lines.extend(["", "---", "提取说明："])
        lines.extend(f"- {warning}" for warning in article["warnings"])
    return "\n".join(lines).rstrip() + "\n"


class ToutiaoChannel(Channel):
    name = "toutiao"
    description = "今日头条公开文章"
    backends = ["内置头条文章读取器"]
    tier = 0

    def can_handle(self, url: str) -> bool:
        from agent_reach.readers.toutiao import ToutiaoReadError, normalize_article_url

        try:
            normalize_article_url(url)
        except (ToutiaoReadError, ValueError):
            return False
        return True

    def check(self, config=None):
        # Installation and article accessibility are separate capabilities.
        # Doctor must not fetch arbitrary articles just to make this row green.
        self.active_backend = None
        return "warn", (
            "指定链接读取器已提供，未联网验证；"
            "运行 agent-reach read-toutiao URL --json 验收。"
            "不含搜索、视频或账号批量采集"
        )

    def read(self, url: str) -> str:
        from agent_reach.readers.toutiao import read_article

        return format_article(read_article(url))
