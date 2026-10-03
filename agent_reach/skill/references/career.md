# 职场招聘

LinkedIn。

若用户按“实际任务完成”验收且接受桌面操作，缺少MCP配置只表示该入口未就绪；可通过允许的原生 Computer Use 搜索公开岗位，逐项读取职位、公司、链接、职责和要求，保留MCP失败记录。不以搜索摘要替代职位详情；明确访问拒绝未恢复时停止对应访问。

## LinkedIn

```bash
# 获取个人资料
mcporter call linkedin.get_person_profile linkedin_username="username" sections="experience,education"

# 搜索人才
mcporter call linkedin.search_people keywords="AI engineer" location="Shanghai"

# 获取公司资料
mcporter call linkedin.get_company_profile company_name="openai" sections="posts,jobs"

# 搜索职位
mcporter call linkedin.search_jobs keywords="software engineer" location="Remote" max_pages=2
```

> **需要登录**: 首次使用前运行 `uvx mcp-server-linkedin@latest --login`，保存有效登录态。

### Fallback 方案

如果 MCP 不可用，可以用 Jina Reader：

```bash
curl -s "https://r.jina.ai/https://linkedin.com/in/username"
```
