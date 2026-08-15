# 第三方依赖与服务说明

本项目自身代码使用 MIT License。下面列出 `0.2.0` 直接运行依赖的包元数据许可证；完整传递依赖及精确版本由 `uv.lock` 固定，分发和部署者仍应自行完成合规复核。

| 组件 | 固定版本 | 包元数据许可证 | 角色 |
|---|---:|---|---|
| aiohttp | 3.14.3 | Apache-2.0 AND MIT | 受控 HTTP 客户端 |
| charset-normalizer | 3.5.1 | MIT | 字符集识别 |
| ddgs | 9.14.4 | MIT | 无密钥搜索连接器备选 |
| mcp | 2.0.0 | MIT | MCP Python SDK |
| Protego | 0.6.2 | BSD-3-Clause | robots 规则解析 |
| pydantic | 2.13.4 | MIT | 输入与输出校验 |
| trafilatura | 2.2.0 | Apache-2.0 | HTML 正文抽取 |
| yarl | 1.24.5 | Apache-2.0 | URL 规范化 |

外部后端不是本项目代码的一部分：

- SearXNG 使用 AGPL-3.0；自托管、修改或再分发时请单独履行其许可证义务。本项目仅通过操作者配置的 HTTP JSON endpoint 调用它。
- Brave Search 是外部商业 API，需要用户自行取得 Key、遵守服务条款并承担费用。
- DDGS 聚合的搜索上游可能各有使用条款、限流和地区可用性，不能视为 SLA 或完整索引。
- DeepSeek API 是模型服务，不是 MCP Host；使用者需自行遵守其服务条款并在宿主侧执行工具参数校验。

本文件是工程信息，不构成法律意见。
