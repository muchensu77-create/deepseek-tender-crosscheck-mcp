# 贡献指南

感谢参与改进。这个项目面向需要追溯和人工签核的公开招投标核验场景，因此安全边界与诚实降级优先于召回数量。

## 本地开发

```bash
git clone https://github.com/muchensu77-create/deepseek-tender-crosscheck-mcp.git
cd deepseek-tender-crosscheck-mcp
uv sync --frozen --extra dev
uv run ruff check src tests
uv run pytest -m "not live"
uv build
```

普通测试必须离线，不得使用 API Key、private_data、商购明细或未授权投标材料。真实联网测试必须显式使用 `live` marker，只访问公开页面，并允许因 robots、限流或上游不可用而诚实失败。

## Pull Request 要求

- 说明变更目的、风险边界和验证结果；
- 安全修复需增加相应的离线回归测试；
- 不得引入验证码、登录、访问控制或 robots 绕过；
- 不得把搜索摘要、有 URL 或官网域名描述成已核验证据；
- 新依赖需说明许可证、维护状态、外部 API/密钥要求与部署成本；
- 任何可能自动放行政府项目结论的变更都不接受。

安全漏洞请按 [SECURITY.md](SECURITY.md) 私密报告，不要通过普通 PR 首次披露可利用细节。
