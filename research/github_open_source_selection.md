# AI 官网检索 MCP：GitHub 开源方案调研与选型

核查日期：2026-08-15（Asia/Hong_Kong）

## 结论先行

最终选择不是“下载一个万能 MCP”，而是一个边界清楚的轻量组合：

1. **协议层**：官方 [`modelcontextprotocol/python-sdk` v2.0.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)，MIT，支持 MCP 2026-07-28 与较早协议客户端；
2. **生产搜索连接器**：操作者自托管的 [SearXNG](https://github.com/searxng/searxng)，通过 JSON API 与本服务解耦；
3. **无密钥演示备选**：[DDGS v9.14.4](https://github.com/deedy5/ddgs/tree/v9.14.4)，只作实时演示/开发 fallback，不作可用性承诺；
4. **受控下载**：本项目用 `aiohttp` 自建公网-only resolver、逐跳重定向、大小/超时/频率边界，用 yarl 对齐实际出站 URL 语义，用 [Protego 0.6.2](https://github.com/scrapy/protego/releases/tag/0.6.2) 执行 robots（BSD-3-Clause）；
5. **正文抽取**：[Trafilatura v2.2.0](https://github.com/adbar/trafilatura/releases/tag/v2.2.0)，Apache-2.0；
6. **动态页**：不执行 JavaScript，明确返回降级警告，不引入验证码、stealth 或访问控制绕过。

SearXNG 是生产首选，但不是本 Python 包的链接依赖，而是独立部署的搜索服务。其 AGPL-3.0 义务不能因“HTTP 调用”就被忽略；如修改并对外提供，应由部署方按许可证履行义务。DDGS 让原型在没有密钥、没有 SearXNG 时仍可做真实烟测，但其上游 HTML/接口可能变化或限流，所以不能把它包装成 SLA。

## 调研方法

本次实际检查了各项目的 GitHub 官方仓库、README、LICENSE、releases/tags、issues/security 页面、近期提交，以及项目/厂商官方文档。博客和搜索摘要没有作为核心证据。版本和维护状态是 2026-08-15 的快照，未来接入前仍应复核。

## 四类能力必须分开

| 层 | 做什么 | 本身不做什么 | 例子 |
|---|---|---|---|
| 模型 API | 理解问题、生成 `tool_calls`、综合回答 | 不执行函数，不因配置了函数就自动联网 | [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls) |
| 搜索引擎/连接器 | 根据 query 发现候选标题、URL、片段 | 不证明页面真实性，不等于读过原网页 | SearXNG、Brave Search、DDGS |
| 网页抓取/正文抽取 | 对已知 URL 发起 GET，抽取正文/元数据 | 不负责全网发现，也不等于业务核验 | MCP Fetch、Trafilatura、Crawl4AI、Scrapling |
| MCP 协议层 | 把工具 schema、调用和结果标准化 | MCP 协议本身不提供搜索、抓取或事实保证 | [MCP architecture（2026-07-28）](https://modelcontextprotocol.io/specification/2026-07-28/architecture) |

DeepSeek Chat Completions 不是 MCP 客户端。接入需要宿主做 `tools/list → DeepSeek function schema`、`DeepSeek tool_call → tools/call` 和 `role=tool` 回填；模型给出的函数名与 JSON 参数始终不可信，必须在服务端再次校验。

## 固定维度比较：能力

| 项目 | 类型 | 真实联网搜索 | 中文/政府网站适配 | 官方域名过滤 | URL 与引文 | 动态网页 |
|---|---|---|---|---|---|---|
| [SearXNG](https://github.com/searxng/searxng) | 元搜索引擎 | 是，聚合启用的上游 | 可配 Baidu/Sogou 等，但默认/可用性需逐项实测；没有政府站专项保证 | `site:` 只会传给上游，不是强制边界；必须结果后过滤 | JSON 有 URL/title/content/engines；只是候选，不是核验引文 | 只搜索，不渲染目标页 |
| [Brave Search MCP](https://github.com/brave/brave-search-mcp-server) / REST | 独立搜索索引 + MCP | 是 | 支持 `search_lang`/country/`lang:zh`，中文政务召回仍需实测 | 支持 `site:`，仍须后过滤 | 结构化 URL、title、description；可追溯但未核验 | 发现索引页，不负责任意网页渲染 |
| [DDGS](https://github.com/deedy5/ddgs) | Python 元搜索库，另含 MCP | 是，聚合多个连接器 | 有 `cn-zh`；实际质量随连接器变化 | 查询可加 `site:`，必须 wrapper 后过滤 | 返回 title/href/body；不是证据 | 自带 extract，但不作为本项目安全抓取器 |
| [官方 MCP Fetch](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch) | 抓取 + MCP | **否** | 通用静态 HTML，无中文政府站专项 | 无强制官网 allowlist | 读取调用者给的 URL；不建立搜索来源链 | 不执行 JS |
| [ihor-sokoliuk/mcp-searxng](https://github.com/ihor-sokoliuk/mcp-searxng) | 社区 SearXNG MCP + URL reader | 通过 SearXNG | 取决于 SearXNG 配置 | 未形成符合本题的操作者官网 allowlist | 搜索 URL + Markdown/PDF reader | 可接 FlareSolverr/Byparr，但本题必须禁用 |
| [Firecrawl](https://github.com/firecrawl/firecrawl) | 搜索 + 抓取 + API/MCP | **是**，四个抓取方案中唯一原生全网 search | Unicode/位置参数、JS 能力强；中国官网覆盖无承诺 | `includeDomains`/`excludeDomains`，仍应后过滤 | URL/title/description，可选正文；候选链较完整 | 强，云端最好；自托管能力/组件有差异 |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | 浏览器抓取/深爬 | **否**，只有已知 URL/站内发现 | Chromium 对编码和 JS 友好 | 有 `DomainFilter`/站内策略，但不是安全出站边界 | 保留 URL/status/HTML/Markdown/metadata | 强，支持等待、滚动、交互 |
| [Scrapling](https://github.com/D4Vinci/Scrapling) | 抓取、浏览器、内置 MCP | **否** | 静态/浏览器双路径，中文无专项基准 | Spider 有 `allowed_domains`；robots 默认需显式开启 | Response 可保留 URL；不是引文系统 | 强，并含 stealth/CAPTCHA/代理/CDP |
| [Trafilatura](https://github.com/adbar/trafilatura) | 正文/元数据抽取、轻量站内 crawl | **否** | CJK、编码、正文/表格处理较好，适合静态中文官网 | 有 URL filter/站内 crawl，但不是 SSRF 安全边界 | 能提取 title/date/text，并可获最终 URL | 不渲染 JS，适合诚实降级 |

## 固定维度比较：工程、许可与风险

| 项目 | 部署难度 | 外部 API 依赖 | 许可证 | 维护活跃度快照 | 主要安全/维护风险 | 当前 Python 本地服务集成成本 |
|---|---|---|---|---|---|---|
| SearXNG | 中；生产常需容器、配置、limiter/Valkey | 可无 key，但依赖各上游引擎 | [AGPL-3.0](https://github.com/searxng/searxng/blob/master/LICENSE) | 2026-08-14 仍提交；无正式 releases/tags，滚动主干 | 公共实例常禁 JSON；上游会 CAPTCHA/封锁；需 pin commit/digest 和履行 AGPL | 低：HTTP JSON adapter |
| Brave Search MCP/REST | MCP 为 Node 22；REST 很轻 | **必须 API Key**，有费用/SaaS | [MIT](https://github.com/brave/brave-search-mcp-server/blob/main/LICENSE) | [v2.1.0，2026-07-18](https://github.com/brave/brave-search-mcp-server/releases/tag/v2.1.0)；8 月仍更新 | HTTP transport 有无认证/费用放大开放 issue；应优先 stdio 或直接 REST + 自有 wrapper | REST 低；直接接 Node MCP 中 |
| DDGS | 很低，`pip install` | 无 key，但依赖/抓取第三方搜索服务 | [MIT](https://github.com/deedy5/ddgs/blob/main/LICENSE.md) | tag v9.14.4，2026-05-15；仓库约 875 commits | README 明示 educational use；连接器易受页面变化、限流、条款影响；实测有瞬时失败 | **最低**，适合开发/演示 fallback |
| 官方 MCP Fetch | 很低，uvx/pip/Docker | 无 | [子目录 MIT](https://github.com/modelcontextprotocol/servers/blob/main/src/fetch/LICENSE) | release [2026.7.10](https://github.com/modelcontextprotocol/servers/releases/tag/2026.7.10)，子目录 7 月仍更新 | README 明示可访问内网；[SSRF issue #4143](https://github.com/modelcontextprotocol/servers/issues/4143) closed-not-planned，修复 PR 未合并；无流式硬大小上限 | 代码集成低，但安全重写成本高，故不直接用 |
| mcp-searxng 社区实现 | 中，Node 20+/NPX/Docker | 需 SearXNG；solver 可选 | [MIT](https://github.com/ihor-sokoliuk/mcp-searxng/blob/main/LICENSE) | [v1.15.0，2026-08-11](https://github.com/ihor-sokoliuk/mcp-searxng/releases/tag/v1.15.0) | 安全文档较好，但 robots/官网 allowlist 不满足；solver 与禁止绕过冲突 | 中，增加 Node 运行栈；只作安全设计参考 |
| Firecrawl | Cloud 低；自托管高，多服务/浏览器/队列/存储 | Cloud 要 key/credits；自托管基础抓取可无 | 核心 [AGPL-3.0](https://github.com/firecrawl/firecrawl/blob/main/LICENSE)，部分 SDK/UI MIT | 正式 [v2.11.0 Release](https://github.com/firecrawl/firecrawl/releases/tag/v2.11.0)（2026-06-19）；v2.11.200 tag 为 2026-08-15，极活跃 | 组件和攻击面大；[advisories](https://github.com/firecrawl/firecrawl/security/advisories) 中 2026 有 RCE/任意文件读取、2024–2025 有 SSRF；自托管版本漂移快 | Cloud SDK 低；自托管高，不适合本次轻量 MVP |
| Crawl4AI | 中/高，完整能力需 Chromium/Playwright；Docker API 更重 | 基础无需模型 API | 仓库标 Apache-2.0，但 [LICENSE](https://github.com/unclecode/crawl4ai/blob/main/LICENSE) 附额外显著署名要求 | [v0.9.2，2026-07-15](https://github.com/unclecode/crawl4ai/releases/tag/v0.9.2)，活跃 | 2026 Docker API 有多起 RCE/SSRF/LFI，0.9 后虽安全默认增强仍不宜暴露通用能力 | Python 原生但较重；许可措辞需法务复核 |
| Scrapling | parser 低；fetchers/MCP/browser 中高 | 无强制 API，但可接代理/CDP | [BSD-3-Clause](https://github.com/D4Vinci/Scrapling/blob/main/LICENSE) | [v0.4.14，2026-08-10](https://github.com/D4Vinci/Scrapling/releases/tag/v0.4.14)，活跃 | stealth、Turnstile/CAPTCHA、代理能力易偏离合规边界；无 SECURITY.md | 静态中，完整模式高；本题不选 |
| Trafilatura | **低**，纯 Python，无浏览器/数据库 | 无 | [Apache-2.0](https://github.com/adbar/trafilatura/blob/master/LICENSE)（早期版本曾是 GPL，必须锁 2.x） | [v2.2.0，2026-07-31](https://github.com/adbar/trafilatura/releases/tag/v2.2.0)，持续更新 | 没有 MCP 级 SSRF 边界；短文 Markdown 结构仍有 issue；因此只接收已安全下载的 bytes 并输出纯文本 | **最低** |

## 关键仓库与上游事实

### SearXNG

- 官方 [Search API](https://docs.searxng.org/dev/search_api.html) 支持 `/` 或 `/search`、GET/POST 和 `format=json`；文档明确许多公共实例会禁用 JSON 并返回 403。
- `site:` 可能原样传给不支持该语法的上游，不能当 allowlist；本实现始终在结果返回后再解析 hostname 并做边界匹配。
- [Docker/Podman 安装](https://docs.searxng.org/admin/installation-docker.html)与 [limiter](https://docs.searxng.org/admin/searx.limiter.html)表明生产部署并非单一 pip 包。
- SearXNG 是元搜索，不拥有统一全网索引；上游改版、限流、验证码会影响可用性。

### Brave

- 当前应使用 Brave 官方 [`brave/brave-search-mcp-server`](https://github.com/brave/brave-search-mcp-server)，不是 2025-05-29 已归档的旧 [`modelcontextprotocol/servers-archived` Brave server](https://github.com/modelcontextprotocol/servers-archived/tree/main/src/brave-search)。
- [官方 REST 文档](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)返回结构化标题、URL、描述；认证头为 `X-Subscription-Token`，密钥不能入库。
- `site:` 与语言参数改善搜索，但安全边界仍必须由本服务做结果后过滤。
- HTTP 模式存在 [无认证 issue #327](https://github.com/brave/brave-search-mcp-server/issues/327)和 [批处理费用放大 issue #277](https://github.com/brave/brave-search-mcp-server/issues/277)，所以若采用应使用 stdio，或把 REST 调用封装在本地服务中。

### Bing/Microsoft

- 微软已宣布 [Bing Search APIs 于 2025-08-11 全面退役](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement)。社区 [`leehanchung/bing-search-mcp`](https://github.com/leehanchung/bing-search-mcp)无 release、最后活跃早且失去可用上游，因此淘汰。
- Azure Grounding with Bing 是托管模型工具，不是可给 DeepSeek 本地调用的独立开源 MCP；普通 Grounding 不能限定具体域，原始工具输出也受限制。

### 抓取/抽取方向

- 官方 [MCP Fetch README](https://github.com/modelcontextprotocol/servers/blob/main/src/fetch/README.md)明确警告可访问 local/internal IP。本题必须连接时验证并 pin 公网 IP、逐跳校验、流式限量，因此不能直接照搬。
- [Firecrawl Search API](https://docs.firecrawl.dev/api-reference/endpoint/search)有真实 search 与 `includeDomains`，但自托管栈和安全/许可成本不适合夜间最小原型。
- Crawl4AI 的浏览器动态能力强，但不是搜索引擎；且历史安全公告与附加署名条款使公开产品接入需要额外审计。
- Scrapling 的 [MCP/动态抓取能力](https://scrapling.readthedocs.io/en/latest/ai/mcp-server.html)很强，但同一能力面含 stealth/验证码/代理，和“不绕过访问限制”冲突。
- Trafilatura 只负责对本项目已下载的 bytes 做抽取；不调用其 downloader，从而不把 SSRF 边界外包给抽取库。

## 最多 3 个候选架构

### 候选 1（最终选择）：Python 薄 MCP + SearXNG/DDGS adapter + 自有受控 fetch + Trafilatura

优点：

- 本地 Python 集成最低，官方 Python SDK 2.0.0 可直接返回结构化 Pydantic 输出；
- SearXNG 可自托管、无逐次 API key，中文引擎可按现场网络调整；
- DDGS 提供无密钥真实演示 fallback，fixture 提供完全离线且明确标注的测试；
- 域名、DNS/IP、robots、重定向、证据状态全部由一个窄服务强制；
- 动态页能力不扩张到 stealth/验证码绕过。

缺点：SearXNG 需要独立运维且是 AGPL；DDGS 无 SLA、会抖动；静态 fetch 不能渲染 JS。

### 候选 2：Brave Search REST + 同一 Python 受控 fetch

优点：独立索引、结构化稳定、无需自管搜索集群，Python REST 集成很低。

未选原因：必须使用 API key、存在持续成本与 SaaS 依赖；中文政务召回需额外验证；现场若 HTTP MCP 暴露还会有认证和费用放大风险。实现仍保留 `brave` adapter，部署方有密钥时可切换。

### 候选 3：Firecrawl 搜索/抓取一体化

优点：搜索、域名参数、动态渲染、正文提取一体化，云端开发体验最好。

未选原因：Cloud 要 key/credits；自托管是明显更重的多服务系统，核心 AGPL-3.0，近期安全修复和快速版本漂移增加审计负担。它适合后续有专人运维的增强版，不适合本次最小可靠 MCP。

## 为什么不选其他方案

- **官方 MCP Fetch**：不是搜索服务器，而且当前 SSRF/响应大小边界不满足本题。
- **旧 MCP Brave**：仓库已归档；应使用 Brave 官方新仓或 REST。
- **Bing MCP**：上游 API 已退役。
- **Azure Grounding**：是 Azure 模型/Agent 工具，不是 DeepSeek 本地开源 MCP，原始证据可见性也不匹配。
- **社区 mcp-searxng**：活跃且安全设计值得参考，但引入 Node，未满足本项目 robots/操作者域名白名单，并暴露本题禁止的 solver 路径。
- **Crawl4AI**：动态页强但无全网搜索，运行和安全面偏大，许可证附加条款需额外确认。
- **Scrapling**：能力面包含 CAPTCHA/stealth/代理，与明确禁止绕过的政策冲突。
- **Trafilatura 单独使用**：只能抽取已知 URL，不能让系统自动发现官网。

## 许可与维护判断

“开源”不等于“无条件可商用、无运维风险”：

- SearXNG、Firecrawl 的 AGPL-3.0 需要部署方认真处理网络服务与修改源码义务；
- Crawl4AI 虽标记 Apache-2.0，但 LICENSE 有额外公开使用署名要求，不能只看 GitHub 的 SPDX badge；
- Trafilatura 必须锁定当前 Apache-2.0 的 2.x，不能混淆早期 GPL 版本；
- DDGS 虽是 MIT，但 README 的 educational-use 声明、第三方搜索服务条款和易变连接器使它更适合演示 fallback；
- Protego 0.6.2 是 BSD-3-Clause，该版修复了 robots 恶意通配模式导致的 CVE-2026-55520 ReDoS，不应降级；
- SearXNG 没有正式 release/tag，生产应 pin 容器 digest 或 commit，不能无审计追随 `latest`。

## 最终实施边界

本原型能做到“实际联网搜索公开官网候选，并受控读取原网页”；它不能做到：

- 保证搜索索引完整、排名正确或页面永远可达；
- 自动绕过验证码、登录、403、429 或 robots；
- 对 JS-only 页面伪装成完整抽取；
- 因为有 `.gov.cn`、TLS、URL 或 SHA-256 就自动宣称事实已核验；
- 让模型任意制造 URL 后再访问。默认 fetch 只接受同进程近期搜索结果中的规范 URL。

真实烟测、测试结果和运行方式见项目根目录 `README.md`。
