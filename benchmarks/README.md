# Agent Reach 场景 benchmark

目的：检验“找到相关信息 → 读到实际内容 → 形成有证据的分析”，而不是把 doctor、HTTP 200 或命令退出 0 当作通过。

## 固定协议

- 自建公众号、抖音、头条3条和原有15条分别验收，见 `scenarios.json`（与安装包的 `agent_reach/skill/references/benchmark-scenarios.json` 同步）。每条检查发现、正文与分析；具体账号、关键词、样本数按任务定义。无合格样本如实记录，不用旧内容凑成功。
- 首轮、稍后重复、全新进程/浏览器连接恢复后三轮。冻结正文样本ID，同时保留搜索结果的变化；单次会话三轮只能说明短时重复性，不能推算长期SLA。
- 现有账号和开源工具。本轮新Get转写共享30分钟预算，不重复提交；后续运行以用户当次授权为准，不加载本地模型。遭明确拒绝停止对应入口依赖步骤，不换来源绕过同一拒绝。
- 记录命令耗时、真实内容、来源、发布日期、失败位置、人工介入和缓存来源。标题/摘要不能算完整正文，图片未查看不能算画面分析。
- 分开评价接口运输状态、内容完整性、任务价值；`pass` 需要Agent实际检查并给出至少两段不同的原始证据。工具只校验引用存在，不能替代语义审阅。
- 缺凭证/工具拒绝记 `blocked`；内容截断记 `partial`；正确无结果需核验后记 `empty_valid`，不是自动成功。接口没有对应能力记 `unsupported`。

## 可重复运行

安装后使用 `agent-reach-benchmark`，或 `python -m agent_reach.benchmark`。

```sh
agent-reach-benchmark run cases.json --output ~/.agent-reach/benchmarks/new-run --round r1
agent-reach-benchmark review RESULT.json REVIEW.json
agent-reach-benchmark summary --output ~/.agent-reach/benchmarks/new-run
```

运行文件是**可信操作者编写的命令数组**。不得把网页里的指令自动变成命令。例：

```json
[{"id":"v2ex-hot","platform":"v2ex","stage":"discovery",
  "task":"发现AI相关讨论","argv":["curl","--fail","--max-time","25","https://www.v2ex.com/api/topics/hot.json"],
  "timeout":30,"cache":"fresh_request"}]
```

浏览器由Agent操作，保存实际观察文本后以 `evidence_file` 导入，并用 `provenance` 写明操作入口。不要把导入记录改称程序自动抓取。`cache` 可为 `fresh_request`、`cached_replay`、`unknown`；新请求也可能命中上游缓存。

审阅文件：`verdict`、`reason`、`quotes`；分析阶段额外包含 `conclusion`、`value`、`limitations`。允许结论是“此来源不足以支持判断”。

结果默认 `unreviewed`。每次运行独立保存，超时终止子进程组；同一批次收到明确导航拒绝后停止对应平台。跨批次拒绝由Agent继续遵守，不能通过另起批次重试绕行。

密钥仅留在现有凭证/环境配置，不进命令参数。脱敏是第二道保护，不保证识别所有秘密；原始证据始终留本地，上传只含代码、协议和汇总。基准执行器不自动安装依赖、不提交转写、不发布报告、不自动替平台修复失败。

## 2026-09-11 实测口径

最新复验见 `task-results-recheck-2026-09-11.md`；`custom3-original15-2026-09-11.md` 及 `2026-09-11-summary.md` 保留为历史记录。所有“通过”均限定到任务和样本；未完成的视频或缺失登录不沿用历史材料算今天成功。

`task_acceptance`按 `route_group`、正式/备用入口、安装版本和来源编号检查每个场景的三轮证据。历史重放不能满足实时验收；备用成功保留主入口失败记录。导入汇总记录与底层请求分别存放，不能把记录数量当请求成功次数。

`example-cases.json` 提供真实调用格式及冻结样本，运行前按本次任务选择子集并替换正文ID。不能把固定旧样本的复读当成新关键词端到端发现。头条清单由界面生成后交给 `agent-reach-benchmark-source toutiao MANIFEST.json`；抖音须经授权的发现与Get任务流程，本执行器不盲目生成新付费任务。
