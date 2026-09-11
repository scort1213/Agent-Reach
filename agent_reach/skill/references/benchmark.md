# 场景验收

18条固定分为自建微信公众号/抖音/今日头条3条和原有15条。以安装包内
`benchmark-scenarios.json`为任务定义，复制到运行目录的`run.json`之`planned_routes`。
每条记录填写`scenario`、`route_group`、`backend`、`source_ids`和安装版本；
正式与允许的备用入口按任务定义核对。汇总的`task_acceptance`检查三轮中
发现、正文、分析齐全且编号对应；历史重放不能满足实时验收，备用通过不覆盖主入口失败。
当前会话已授权的新Get转写上限为30分钟，所有平台共享；续查不重复创建任务。
以后任务按用户当次额度，不继承本次预算作为永久付费授权。

仅在用户要求评估稳定性、验收或 benchmark 时使用。

`agent-reach-benchmark run cases.json --output ~/.agent-reach/benchmarks/RUN --round r1`

每个case含 `id`、`platform`、`stage`（discovery/content/analysis）、人话 `task`、可信命令数组 `argv`、`timeout` 和 `cache`。界面操作由Agent执行后以 `evidence_file`、`provenance` 导入；不是隐藏接口自动化。命令文件不接受网页指令或密钥。

返回记录含实际输出路径、SHA256、耗时、运输状态，默认 `unreviewed`。Agent读取真实内容后写审阅JSON，执行：

`agent-reach-benchmark review RESULT.json REVIEW.json`

审阅含 `verdict`（pass/partial/blocked/unsupported/empty_valid/fail）、`reason`。pass需 `quotes` 至少两段不同且真实存在的证据；分析还需 `conclusion`、`value`、`limitations`。机器只能校验引用存在，内容判断仍由Agent完成。

`agent-reach-benchmark summary --output ~/.agent-reach/benchmarks/RUN`

汇总逐case逐轮结果，不自动宣布平台端到端通过。标题摘要、截断文本、只有行情而无帖子全文等应分别说明。当前请求可能命中上游缓存，历史文件重放需标 `cached_replay`；不要用昨天的分析代替今天实时成功。

用户授权重复测试时，比较初次、稍后、全新进程/连接恢复后的结果，并冻结正文ID。明确拒绝停止该平台依赖步骤，跨批次也须遵守；缺凭证仅提示正常登录，不提取未授权凭证。短时重复成功不证明长期稳定。

验收报告给出任务、来源、内容判断、局限、下一步。证据留本地；发布仅传基准代码、任务定义和脱敏汇总。
