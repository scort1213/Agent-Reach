# 网页阅读

今日头条关键词/账号批量阅读、指定文章、通用网页、RSS。

## 今日头条关键词与账号

用户只需说「找人工智能最近24小时的5篇头条文章并分析」或「分析中国网信杂志的前5篇文章」。
列表发现使用当前宿主提供的浏览器工具；不另启 Playwright/读取隐藏应用状态，不声称独立 CLI 可以搜索。

1. 关键词：打开头条首页，用搜索框搜索，检查新开的 `so.toutiao.com` 标签页，切换「资讯」。优先使用可见时间筛选；没有时间筛选时按结果卡片日期初筛，再用正文接口发布时间复核。默认最近24小时，日期不明不算近期。
2. 账号：打开用户提供或搜索界面确认的主页，核对名称与认证；同名且无法确定才询问。等待「文章」标签/真实列表，不把刚出现的加载失败、粉丝0当最终状态。等待后仍失败可刷新一次；验证码或拒绝访问则停止。
3. 只采目标账号主列表/关键词资讯结果，不收旁栏推荐、热榜、视频、微头条。按文章编号去重、保留展示顺序，直到指定数量或列表末尾。连续3次没有新条目则标记未完成，保存已发现条目；不把停滞当作抓全。
4. 保存 JSON 清单，使用本机批量读取器，最后只分析 `status=success` 的真实正文。关键词数量不足如实交付，不能加旧文凑数。搜索/账号列表的可见范围不代表所有历史。

清单格式（时间必须包含时区）：

```json
{"mode":"keyword","query":"人工智能","captured_at":"2026-09-10T14:00:00+08:00","list_url":"实际搜索页URL","discovery_complete":false,"items":[{"title":"页面上的完整标题","url":"实际发现的文章链接"}]}
```

账号将 `mode` 设为 `account`，`query` 为账号名；默认不限制日期。可附加 `source`、`date_text`、`account_url`、`observed_url` 等证据字段。链接支持实际观察到的头条 `/article/ID/`、`/iID/`、`/aID/` 和 `so/sou.toutiao.com/search/jump`，不会请求任意重定向目标。

```bash
agent-reach read-toutiao-batch candidates.json --output ./toutiao-run --limit 5
```

关键词默认24小时，也可 `--hours 72`；筛选基准是清单采集时间。默认最多读取20篇，`--limit` 控制最多尝试读取的去重候选数。每次任务使用独立输出目录；原清单和原参数重跑自动复用已处理结果，失败不会被悄悄重提。修改清单/参数需要新目录。

交付 `batch.json` 与逐篇 JSON/Markdown。报告搜索范围、候选数量、正文成功数量、失败原因；`discovery_complete` 与 `processing_complete` 分开，处理完一批不等于全历史抓全。退出码0表示所选批次处理正常（可能无近期结果），2表示正文读取/日期等未通过，1表示输入或文件错误。

## 今日头条指定文章

头条文章链接优先使用本 Fork 的读取器：

```bash
agent-reach read-toutiao "https://www.toutiao.com/article/ARTICLE_ID/" --json
```

- 成功：`ok: true`，包含标题、来源、发布时间（可能缺失）、正文、图片引用、原链接、获取时间和读取方式。
- 失败：`ok: false`，错误代码和原因，进程非零退出。不要把失败、验证码、空壳页解释成“文章没有内容”。
- 此单篇命令只读指定公开文章；关键词和账号走上述浏览器发现＋批量读取流程，不支持视频或评论采集。
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
