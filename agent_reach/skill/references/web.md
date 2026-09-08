# 网页阅读

今日头条文章、通用网页、RSS。

## 今日头条指定文章

头条文章链接优先使用本 Fork 的读取器：

```bash
agent-reach read-toutiao "https://www.toutiao.com/article/ARTICLE_ID/" --json
```

- 成功：`ok: true`，包含标题、来源、发布时间（可能缺失）、正文、图片引用、原链接、获取时间和读取方式。
- 失败：`ok: false`，错误代码和原因，进程非零退出。不要把失败、验证码、空壳页解释成“文章没有内容”。
- 按指定链接取公开文章；不支持头条关键词搜索、账号全量、短链接自动发现、视频或评论采集。
- 移动文章接口为首选，普通超时/解析失败时可尝试 Jina。明确的权限、验证、限流或宿主策略拒绝后停止，不改用通用网页命令绕过。
- 图片只保留引用；正文提取不代表图中内容、视频或全部动态内容已被识别。Jina 结果可能来自缓存，按输出说明引用。
- Doctor 的“未联网验证”不表示未安装；用户明确请求某篇文章时，再执行该条读取命令。

不带 `--json` 时输出便于阅读和保存的 Markdown。

## 通用网页 (Jina Reader)

```bash
# 读取任意网页内容
curl -s "https://r.jina.ai/URL"

# 示例
curl -s "https://r.jina.ai/https://example.com/article"
```

**适用场景**: 大多数网页可以直接用 Jina Reader 读取。

## Web Reader (MCP)

```bash
# 读取网页内容 (Markdown 格式)
mcporter call web-reader.webReader url="https://example.com"

# 保留图片
mcporter call web-reader.webReader url="https://example.com" retain_images=true

# 纯文本格式
mcporter call web-reader.webReader url="https://example.com" return_format="text"
```

**适用场景**: 需要更精确控制输出格式时使用。

## RSS (feedparser)

```python
python3 -c "
import feedparser
for e in feedparser.parse('FEED_URL').entries[:5]:
    print(f'{e.title} — {e.link}')
"
```

**适用场景**: 订阅博客、新闻源、播客等 RSS feed。

## 选择指南

| 场景 | 推荐工具 |
|-----|---------|
| 今日头条公开文章链接 | `agent-reach read-toutiao URL --json` |
| 通用网页 | Jina Reader (`curl r.jina.ai`) |
| 需要图片/格式控制 | web-reader MCP |
| RSS 订阅 | feedparser |
