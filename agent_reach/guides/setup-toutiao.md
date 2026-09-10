# 今日头条：搜索、账号列表与正文读取

本 Fork 自带一个轻量的头条文章读取器，随 Agent Reach 包安装，不需要额外浏览器、账号或付费密钥。
网络可访问性仍需用具体文章验收，安装本身不代表每篇文章都可读取。

```bash
agent-reach read-toutiao "https://www.toutiao.com/article/7590303258061767194/"
agent-reach read-toutiao "https://www.toutiao.com/article/7590303258061767194/" --json
```

第一条输出 Markdown；第二条输出 JSON，可交给 Agent 继续分析或保存。需要保存时使用终端重定向：

```bash
agent-reach read-toutiao "文章链接" --json > article.json
```

JSON 成功结果的 `ok` 为 `true`。主要字段：`title`、`source`、`published_at`、`content`、
`text`、`images`、`url`、`article_id`、`retrieved_at`、`backend` 和 `warnings`。
缺失的元数据保持缺失；`retrieved_at` 是获取时间，不能当作发布时间。

失败时退出码为 1，JSON 的 `ok` 为 `false`，`error` 说明失败原因。不要分析错误消息，
也不要拿另一个网站的文章替代这个链接的正文。

读取链路是“校验头条文章 URL → 移动文章数据 → 提取正文并检查”；普通接口或网络故障
才尝试 Jina。明确的登录、访问拒绝、验证码、限流或宿主工具策略阻断不会通过替换服务绕过。
Jina 可能使用缓存，读取成功不证明源站此刻重新抓取成功。

单篇命令支持包含数字文章编号的头条文章链接。图片保留引用，未自动下载或识别；纯图片文章需留意输出说明。

## 关键词和账号批量入口

在有浏览器工具的 Agent 中说「搜索人工智能最近24小时的5篇头条文章并分析」，
或「采集中国网信杂志前5篇图文并分析」。Agent 按随包 Skill 的
[网页阅读流程](../skill/references/web.md) 找列表、保存清单，然后调用：

```bash
agent-reach read-toutiao-batch candidates.json --output ./toutiao-run --limit 5
```

列表发现需要宿主浏览器工具，包本身不会安装浏览器桥接或自动继承登录。批量命令
接收清单，不接受关键词当成清单路径；没有浏览器工具时仍能读指定文章或已有清单。

清单包含 `mode`（keyword/account）、`query`、带时区的 `captured_at`、`list_url`、
`discovery_complete` 和 `items`（每项含 `url`、建议含 `title`）。关键词默认24小时，
用正文发布时间复核；账号默认不限定日期。`--hours` 可调整窗口。`--limit` 默认20，
限制去重候选读取次数，而不是承诺成功文章数；不足或失败均如实报告。

结果保存在 `batch.json` 和逐篇 JSON/Markdown。使用相同清单和参数重跑复用已处理条目；
更换条件使用新输出目录。单篇失败保留记录；访问拒绝/限流停止整个批次，不换工具绕过。
只有 `status=success` 才进入正文分析；旧文、日期未知、无文字正文分别标注。
处理完一批不表示整账号历史或全网关键词结果已经抓全。微头条、视频和评论仍不支持。

`agent-reach install --channels=toutiao --safe` 会接受该渠道名称，但无需为它安装其他平台依赖。
`doctor` 不自动取文章、不读取浏览器 Cookie，显示“读取器已提供，未联网验证”是有意区分工具存在与内容可读。
