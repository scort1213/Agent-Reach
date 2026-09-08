"""Optional keyword listings, without advertising account or body collection."""

from agent_reach.wechat import SETUP_HINT, probe_runtime

from .base import Channel


class WeChatChannel(Channel):
    name = "wechat"
    description = "公众号关键词搜索（搜狗收录）"
    backends = ["wechat-article-search"]
    tier = 1

    def can_handle(self, url: str) -> bool:
        # Keyword search does not implement article-URL reading.
        return False

    def check(self, config=None):
        self.active_backend = None
        if not probe_runtime():
            return "off", SETUP_HINT
        return "warn", (
            "wechat-article-search 本地解析检查通过；搜狗网络/验证状态未实时验证。"
            "用 agent-reach search-wechat '关键词' --json 执行一次搜索。"
        )
