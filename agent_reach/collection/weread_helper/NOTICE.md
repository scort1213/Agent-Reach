# 来源与许可

本工具以 AGPL-3.0 发布；许可证全文见 LICENSE。

微信读书登录、续期、公众号目录和正文请求流程参考：

- finlater/weread.koplugin，AGPL-3.0。
- 审查版本：2943080c2493a1ae262cc97c74908ed62928c935。
- https://github.com/finlater/weread.koplugin
- 相关文件：weread/lib/client.lua、登录和公众号接口验证脚本。

2026-09-09 新增：独立 Python 请求与解析、本机网页界面、登录保存、批量任务、文字导出、错误检查及测试。不包含上游登录凭证或第三方文章正文。

第三方 Python 依赖独立许可：Requests（Apache-2.0）、qrcode（BSD）、Pillow（MIT-CMU）、Beautiful Soup（MIT）；其余传递依赖见各安装包元数据。未修改或重新许可这些依赖。
