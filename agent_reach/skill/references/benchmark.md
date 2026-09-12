# 场景验收

18条固定分为自建微信公众号/抖音/今日头条3条和原有15条。以安装包内
`benchmark-scenarios.json`为任务定义，复制到运行目录的`run.json`之`planned_routes`。
每条记录填写`scenario`、`route_group`、`backend`、`source_ids`和安装版本；
正式与允许的备用入口按任务定义核对。汇总的`task_acceptance`检查三轮中
发现、正文、分析齐全且编号对应；历史重放不能满足实时验收，备用通过不覆盖主入口失败。
当前会话已授权的新Get转写上限为30分钟，所有平台共享；续查不重复创建任务。
以后任务按用户当次额度，不继承本次预算作为永久付费授权。

## 按任务完成验收

场景清单预先声明允许的正式和备用入口；实际任务通过与原工具恢复分别汇报。自建抖音/头条的 `agent_computer_use` 包括原生窗口 `cua_native` 和 Chrome 网页接口 `chrome_browser`；内置浏览器 `iab`、OpenCLI `opencli` 分别检查。工具初始化失败不等于目标访问被拒绝，也不代表其他入口已通过。只有已授权且没有同一目标/宿主明确拒绝的入口可继续；未知错误不自动换路。

桌面操作只由一个 Agent 串行执行。输入关键词或链接后先读取输入框，确认与预期一致，再提交；若粘贴内容不符，记录输入错误并纠正，不能把错误查询当目标无结果。正文、图片和媒体仍须逐项取得并实际阅读。

新验收清单使用 `acceptance_schema: 2`。每条记录补 `actual_entry`、`execution_id`、`round_mode`；第三轮使用新执行进程，以 `resume_from` 关联同一验收目录内此前有效 r1 或 r2、同场景同阶段的 `observation_id`，排除自身、将来轮次、其他验收目录和历史重放。执行编号来自实际工具调用或进程，导入编号不能冒充上游执行编号。初始化、认证、适配器、内容、明确拒绝和未知错误用 `failure_kind`、`failure_stage`、`failure_scope` 分开记录。普通失败后恢复关联 `recovery_of`，明确拒绝另需正常恢复的真实证据，不能只改标签。

内容审阅须核对完整正文并记录 `content_complete`；分析审阅绑定实际正文、报告和需要的画面文件及哈希，需看图的场景记录 `visual_checked`。较晚失败不能被同一入口的旧通过遮盖。旧清单/报告保持可读，但历史证据不能自动满足新一轮实时验收。

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

普通 Navigation rejected 不自动归类 access_denied：它可能来自浏览器导航状态。先按实际错误记录 unknown、failure_stage 和 actual_entry；只有明确的工具策略/权限拒绝或 Agent 核对的拒绝证据才记 access_denied。原有拒绝记录不自动改成通过，保留更正说明并另做真实复验。空结果处理、历史内容复核、全新内容处理与重试后成功分别统计。

### 重复读取中的动态内容

保存原始内容哈希；全文差异需要检查，不能一律判正文变更或一律忽略。图片 CDN 域名/过期签名变化时，可另记正文文字和图片资源路径的一致性，同时保留原始差异。发现清单、正文、分析报告分别计数。仅两条帖子有可读文字、另一条只有短链接时，不能写三条正文分析成功；图文只查看封面或视频只查看个别截图，也须明确覆盖范围。
