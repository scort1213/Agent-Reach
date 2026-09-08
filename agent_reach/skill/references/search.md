# 搜索工具

Exa 网页搜索与公众号关键词搜索。

## 公众号文章关键词搜索（默认 wechat-article-search）

用户要求按关键词搜索公众号文章时，使用下面的命令。首次缺少依赖时，
`setup-wechat` 只安装该搜索工具锁定的 npm 依赖，需要 Node.js 20.18.1+ 和 npm。

```bash
agent-reach setup-wechat
agent-reach search-wechat "人工智能" --limit 10 --json
# 用户需要更多结果时，显式读取下一页
agent-reach search-wechat "人工智能" --page 2 --limit 10 --json
```

底层使用 `zjp1997720/wechat-article-search` 的文章卡片解析器，固定版本随包分发。
每次只请求一页搜狗微信搜索，不跟随文章链接。返回标题、公众号显示名称
（`source`）、日期、摘要和搜狗链接，缺失字段为 `null`。遇到验证码、非 200
响应或未知页面结构时，退出码为 1、`status: error`，不能说成“没有结果”。

搜索范围受搜狗收录限制。摘要不是正文；不能用这些结果声称读完文章。
输入公众号名称仍然是文章关键词搜索，不是验证账号身份、列出完整历史或
按账号订阅。账号采集尚未纳入，不能自动转用微信读书或公众号后台。
Doctor 只做本地解析检查，因此网络未经验证时 `active_backend` 保持 `null`。
失败应报告具体状态，停止本次搜索；遇到工具访问策略拒绝时，不换通道绕过。

## WeChat keyword search (English)

For WeChat public-account article keywords, use `agent-reach search-wechat
"query" --limit 10 --json`. Run `agent-reach setup-wechat` once to install the
locked optional npm dependencies (Node.js 20.18.1+). Each request reads one Sogou
listing page. Use `--page 2` explicitly for another page. The parser comes from
the pinned `wechat-article-search` project. Fields include title, source account
display name, dates, summary and Sogou link; missing fields are null. Captcha,
HTTP failures and unknown layouts are errors, not empty results. No article
bodies, account identity verification, complete histories or subscriptions are
provided. Respect tool-level access denials; do not switch channels to bypass them.

## Exa AI 搜索

高质量 AI 搜索引擎，适合查找技术文档、官方示例和相关网页。

```bash
mcporter call exa.web_search_exa query="query" numResults=5
mcporter call exa.web_search_exa query="library API code example" numResults=5
```

### 使用场景

| 场景 | 参数 |
|-----|------|
| 网页搜索 | `web_search_exa(query: "...", numResults: 5)` |
| 技术/代码资料 | `web_search_exa(query: "框架名 API 示例", numResults: 5)` |

> Exa MCP 的 `get_code_context_exa` 已弃用且默认不注册。代码问题也使用
> `web_search_exa`；需要精确搜索仓库内容时，改用 `dev.md` 中的 GitHub 搜索。

### 特点

- 擅长英文内容和技术文档
- 可通过查询词定位官方文档和代码示例
- 结果质量高

## 与其他搜索工具对比

| 工具 | 来源 | 适用场景 |
|-----|------|---------|
| Exa | agent-reach | 英文/技术/代码搜索 |
| 智谱搜索 | my-mcp-tools | 中文搜索 |
| GitHub 搜索 | agent-reach (dev.md) | 仓库/代码搜索 |
