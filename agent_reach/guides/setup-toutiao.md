# 今日头条：指定公开文章读取

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

支持范围是包含数字文章编号的头条文章链接。暂不包括短链接展开、首页、关键词搜索、
整账号历史、微头条、视频和评论。图片保留引用，未自动下载或识别；纯图片文章需留意输出说明。

`agent-reach install --channels=toutiao --safe` 会接受该渠道名称，但无需为它安装其他平台依赖。
`doctor` 不自动取文章、不读取浏览器 Cookie，显示“读取器已提供，未联网验证”是有意区分工具存在与内容可读。
