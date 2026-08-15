# DeepSeek 招投标公开信息双向复核 MCP

[![CI](https://github.com/muchensu77-create/deepseek-tender-crosscheck-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/muchensu77-create/deepseek-tender-crosscheck-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

独立 Python MCP 原型，主工具专门面向 DeepSeek 的招投标业绩复核：**先正向发现项目、主体、结果、合同和验收线索，再逆向查更正、终止、废标、质疑投诉和重新采购/招标线索**。整个顺序由服务端强制，不依赖模型自觉“想起来复查”。

当前原型版本 `0.2.0`：可安装、可用 stdio 启动、4 个工具可经官方 Python SDK 2.0.0 调用（支持 MCP 2026-07-28 与较早协议客户端）。它输出可追溯的“待人工复核证据台账”，不输出自动 pass。

## 能力边界

本服务分四层，不把它们混为一谈：

```text
DeepSeek / 平台宿主（收集结构化申报字段）
        │
        ▼
MCPServer（官方 Python SDK 2.0.0，工具 schema 与结构化结果）
        │
　　　 ├── 招投标策略层：固定 F01–F03 正向 → 固定 R01–R05 逆向
　　　 │       └── 字段观察、风险信号、覆盖缺口、人工闸门
　　　 ├── 搜索连接器：SearXNG / DDGS / Brave / fixture
        │       └── 只产出 candidate_unverified
        │
        └── 公网受控 fetch + Trafilatura
                └── 只产出 retrieved_not_verified
```

它不会绕过验证码、登录、403/429、robots 或访问控制，不执行网页 JavaScript，也不会因为有 URL、`.gov.cn`、TLS 或哈希就自动宣称事实已核验。**即使 `system_status=two_pass_completed_pending_human`，也仍必须实名人员签核。**

## 工具

### `search_official_sources(query, domains?, max_results?)`

实际调用所选搜索后端，返回：

- 标题、搜索后端返回的 `original_url`、安全规范化后的 `url`、摘要/片段；
- UTC 查询时间、来源后端、是否真的进行了联网搜索；
- `candidate_unverified` 证据状态；
- 后端已返回但超出 `max_results` 主展示上限的 URL 会进入 `overflow_results`，并记录 `raw_result_count`/`result_limit_hit`，不静默丢弃；
- 过滤数量、HTTP 明文等 warning。

`domains` 只能缩小服务器已授权范围，不能让模型临时扩大 allowlist。`site:` 只用于提高召回，结果 URL 仍会被后置过滤。

### `fetch_official_page(url)`

受控读取页面并返回：

- 请求 URL、每跳重定向、最终 URL、页面 canonical；
- 标题、正文片段、UTC 抓取时间、实际响应 bytes 的 SHA-256；
- content type、bytes 数、抽取方式、动态页降级状态；
- 抽取后总字符数和 `content_truncated`；超过 8,000 字符时明示截断，不当完整正文。
- `retrieved_not_verified` 证据状态。

安全默认值下，URL 必须来自**同一服务进程最近 15 分钟内的一次搜索结果**。这是为了防止模型凭空拼出一个看似官方的 URL。受信平台若能单独证明 URL 直接来自用户，可由操作者设置 `OFFICIAL_SEARCH_ALLOW_DIRECT_FETCH=true`，但不建议给普通模型会话默认开启。

### `search_and_fetch_official(query, domains?, max_results?)`

确定性地串联前两步（最多 3 个候选），每个页面仍嵌套保存原 SearchResult 与独立 FetchResponse，不丢中间来源；一个候选失败不会被伪造为正文。

### `crosscheck_tender_information(...)`

招投标专用工具，一次调用内由服务端按固定顺序执行：

1. F01 按项目身份查中标/成交结果；
2. F02 按项目+供应商/采购人+标包交叉查主体角色；
3. F03 查合同与履约验收记录；
4. 前三条搜索完成并冻结正向候选后，再执行 R01–R05：更正/变更/澄清，终止/中止/暂停/废标/流标/取消/撤销/撤回/作废，质疑/投诉/监督/结果无效，法定主体或信用代码反绑，合同解除/终止、验收不合格、重新采购/招标。八条搜索全部完成后才开始取页，慢页不会挤掉逆向搜索时间。

schema 只强制必填 `project_name`，但要达到最高的机械覆盖状态，还必须有 `project_code`、`package_code`、`supplier_legal_name`、`unified_social_credit_code`。缺任一项仍会搜索并留痕，但只能进入 `insufficient_identifiers` 或更严格状态。纯数字编号少于 8 位（如四位年份）不能作强身份锚点。

三组枚举不能随意造值：

- `procurement_regime`: `government_procurement` / `mandatory_tender` / `unknown`；
- `notice_type`: `tender_notice` / `candidate_notice` / `award_result` / `contract_notice` / `acceptance_record` / `unknown`；
- `claimed_role`: `bidder` / `candidate` / `winner` / `contractor` / `completed_performance` / `unknown`。

输出保留 `run_id`、`server_version`、原始/规范化输入哈希、查询计划哈希、搜索截止与流程完成时间、8 个 `search_runs`、页面与正文哈希、`field_observations`、`risk_signals`、`coverage`、`system_status`、`review_reasons` 和强制 `human_gate`。字段只能从“同页绑定项目名称+项目编号”的完整可读页面观察；编号、标包、金额和日期还用边界规则，避免 `包1`命中 `包10`。最高机械覆盖还要求同一完整页绑定申报标包+供应商法定名+信用代码。这仍不代表主体角色、金额口径、标包归属或法律结论。风险扫描会做 NFKC/空白规范化和受限模式匹配，仍只是待人工核对的文本信号。

MCP/DeepSeek 的第 4 工具 schema **不向模型暴露** `domains`、`max_results_per_query` 或 `max_pages_to_fetch`：来源范围由服务器配置，预算固定为每查询 3 条、全案件最多抓取 10 页。只有受信操作者调用 service/CLI 时才能在服务约束内收窄域名或调低预算。

`system_status` 可能是 `two_pass_completed_pending_human`、`risk_signals_found`、`inconclusive`、`no_candidate_observed`、`insufficient_identifiers` 或 `offline_fixture_only`。`no_candidate_observed` 只表示本次预算/后端/截止时间内未观察到候选，不代表事实不存在。这些都是系统运行/证据状态，**没有 `approved`、`pass` 或“真实有效”这种机器结论**。

## 安装

推荐 Python 3.12 与 [uv](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/muchensu77-create/deepseek-tender-crosscheck-mcp.git
cd deepseek-tender-crosscheck-mcp
uv sync --frozen --extra dev
```

复制 [`.env.example`](.env.example) 中需要的配置到运行环境；不要把真实 API Key 写入 `.env.example`、MCP JSON、提交记录或截图。

`uv.lock` 是可复现部署的权威锁文件；`pyproject.toml`、`requirements.txt` 只列直接依赖，不能替代完整传递依赖锁。政府或审计场景必须使用上面的 `uv sync --frozen`。核心版本：

- `mcp==2.0.0`（官方 Python SDK 稳定版）；
- `ddgs==9.14.4`；
- `aiohttp==3.14.3`；
- `protego==0.6.2`（BSD-3-Clause，已含 robots 通配模式 ReDoS 修复）；
- `trafilatura==2.2.0`；
- `yarl==1.24.5`（Apache-2.0，与 aiohttp 出站 URL 规范化语义对齐）；
- `pydantic==2.13.4`。

GitHub Release 的 wheel 可用于普通评估，但单独 `pip install` 会按当时索引解析传递依赖，不应被当作可复现的高追责生产部署方式。

## 搜索后端

| `OFFICIAL_SEARCH_BACKEND` | 是否实时 | 是否要密钥 | 用途 |
|---|---:|---:|---|
| `auto` | 是 | 视配置 | 有 SearXNG URL 时优先 SearXNG；否则有 Brave key 时用 Brave；再否则用 DDGS |
| `searxng` | 是 | 自托管可无 | **建议生产首选**，操作者控制实例和引擎 |
| `ddgs` | 是 | 否 | 开发/现场无密钥 fallback；连接器会抖动，无 SLA |
| `brave` | 是 | 是 | 托管搜索备选，结构化结果稳定但有成本/SaaS 依赖 |
| `fixture` | **否** | 否 | 离线 CI/演示 schema；响应明确 `live_search_performed=false` |

### 无密钥快速运行（DDGS）

默认 `auto` 在没有其他配置时按顺序尝试 `yahoo,duckduckgo,yandex` 三个 DDGS 连接器：

```bash
uv run official-search-cli search "政府采购 条例" --domain gov.cn --max-results 5
```

该路径是实时联网，但第三方连接器可能限流、改版或暂时无结果。DDGS 连接器在可超时终止的独立子进程中运行，避免其 HTTP 全局补丁污染 MCP 宿主。生产仍请配置 SearXNG，或在有预算时切 Brave。

### 自托管 SearXNG

SearXNG 必须在 `settings.yml` 启用 JSON 格式：

```yaml
search:
  formats:
    - html
    - json
```

然后只把操作者控制的 endpoint 交给本服务：

```bash
export OFFICIAL_SEARCH_BACKEND=searxng
export OFFICIAL_SEARCH_SEARXNG_URL=http://127.0.0.1:8080
uv run official-search-cli search "采购公告" --domain gov.cn --max-results 5
```

本地 SearXNG endpoint 属于操作者静态配置，使用独立受信 client；模型提供的页面 URL 永远走另一个公网-only client，二者不复用。SearXNG 是 AGPL-3.0，部署和修改时应复核许可证义务，并 pin 镜像 digest/commit。

### Brave（可选）

```bash
export OFFICIAL_SEARCH_BACKEND=brave
export OFFICIAL_SEARCH_BRAVE_API_KEY='从运行环境注入，不要写入仓库'
uv run official-search-cli search "采购公告" --domain gov.cn --max-results 5
```

服务的配置摘要只显示“是否配置”，不会显示 key。不要把 key 放进 MCP JSON、提交记录、日志或截图。

### 生产正向/逆向后端

`OFFICIAL_SEARCH_BACKEND` 负责 F01–F03；`OFFICIAL_SEARCH_REVERSE_BACKEND` 可为 R01–R05 指定另一类后端。未设置后者时，逆向复用正向后端，报告会如实给出 `different_reverse_backend_configured=false`。即使该值为 `true`，也只表示后端名不同，不代表官方来源独立；两个后端仍可能使用重叠索引。

有 SLA 需求的生产示例：

```bash
export OFFICIAL_SEARCH_BACKEND=searxng
export OFFICIAL_SEARCH_REVERSE_BACKEND=brave
export OFFICIAL_SEARCH_SEARXNG_URL='http://127.0.0.1:8080'
export OFFICIAL_SEARCH_BRAVE_API_KEY='仅从运行环境注入'
export OFFICIAL_SEARCH_DEFAULT_DOMAINS='gov.cn'
```

无密钥时可用 `searxng` + `ddgs`，但 DDGS 无 SLA，不应把“两个后端”夸大为“无漏检”。当前只有一个 SearXNG URL 配置，不能用同一 URL 伪装成两个独立索引。

### 离线 fixture

```bash
OFFICIAL_SEARCH_BACKEND=fixture \
  uv run official-search-cli search "离线 schema 测试" --domain gov.cn --max-results 2
```

fixture 结果有真实格式但**不是这次运行的实时搜索**。它不会被描述成 live。

## 启动 MCP（stdio）

```bash
uv run official-search-mcp
```

进程无普通 stdout 文本；stdout 是 MCP 协议线。运行日志如后续增加，必须只写 stderr，且不得记录 key 或整页正文。

通用 MCP 客户端配置（替换为本机绝对路径）：

```json
{
  "mcpServers": {
    "official-search": {
      "command": "/absolute/path/to/deepseek-tender-crosscheck-mcp/.venv/bin/python",
      "args": ["-m", "official_search_mcp"],
      "env": {
        "OFFICIAL_SEARCH_BACKEND": "searxng",
        "OFFICIAL_SEARCH_REVERSE_BACKEND": "ddgs",
        "OFFICIAL_SEARCH_SEARXNG_URL": "http://127.0.0.1:8080",
        "OFFICIAL_SEARCH_DEFAULT_DOMAINS": "gov.cn",
        "OFFICIAL_SEARCH_ALLOWED_DOMAINS": ""
      }
    }
  }
}
```

企业/学校等非政府官网必须由操作者预先加入 `OFFICIAL_SEARCH_ALLOWED_DOMAINS`，例如 `example.com,example.org`；前三个通用工具调用中的 `domains` 也只能收窄范围，不能自行授权新域。招投标专用工具不向模型暴露 `domains`。

## CLI 诊断

```bash
# 不泄露 secret 的有效配置摘要
uv run official-search-cli config

# 实时搜索
uv run official-search-cli search "政府采购 条例" --domain gov.cn --max-results 5

# 同一进程内搜索并读取，推荐用于命令行演示
uv run official-search-cli search-and-fetch "政府采购 条例" --domain gov.cn --max-results 1

# 招投标专用：服务端固定 3 个正向 + 5 个逆向，默认最多取 10 页
uv run official-search-cli tender-crosscheck "某公开采购项目" \
  --regime government_procurement \
  --project-code "PROJECT-2026-001" \
  --package-code "01" \
  --purchaser "某采购人" \
  --supplier "某供应商法定全称" \
  --credit-code "91110000123456789X" \
  --notice-type award_result \
  --claimed-role winner \
  --domain gov.cn \
  --max-results-per-query 3 \
  --max-pages 10
```

单独运行 `official-search-cli fetch URL` 会启动新进程，没有先前候选登记，因此默认返回 `url_not_in_search_candidates`。这是安全行为；请用 `search-and-fetch`，不要为了方便全局关闭来源检查。

## 安全设计摘要

- 仅允许 `http`/`https`，拒绝 userinfo、IP literal、控制字符、反斜杠与非常规端口；
- 域名 IDNA/小写/尾点规范化，使用 `host == domain` 或 `.domain` 子域边界匹配；候选登记、robots 匹配、重定向证据和 aiohttp 线上请求共用 yarl 规范 URL，避免 `..`/百分号编码语义差异；
- DNS 在实际连接 resolver 中解析并固定，A/AAAA 任一非 global 即拒绝，覆盖 loopback、RFC1918、CGNAT、link-local、multicast、reserved、unspecified、IPv4-mapped IPv6；
- `trust_env=False` 且使用 `DummyCookieJar`，不接受或保存 cookie，不接受模型传入 Authorization、proxy、自定义 header、脚本或 CDP；
- 禁库自动跳转，最多 3 跳，每跳重新校验搜索当次选定域范围、DNS/IP；拒绝 HTTPS→HTTP 降级；
- 明确 UA，用 Protego 按最长规则处理 robots 通配符/末尾，对每个目标路径和每跳重定向重新判定；执行 Crawl-delay/Request-rate，无法可靠执行 Visit-time 时 fail-closed；404/410 可放行，401/403/429/5xx/网络失败 fail-closed；不提供 ignore 开关；
- 响应最多 2 MiB，返回正文最多 8,000 字符；超长时返回 `extracted_char_count` + `content_truncated=true`，不计完整取页，因为未展示尾部可能有终止信息；连接/读取/整个操作超时、全局并发、每分钟频率与单域间隔都有上限；Trafilatura 在可强制终止的隔离子进程中抽取；
- 只接受 HTML/XHTML/纯文本；PDF、图片、压缩包等返回解释性错误；
- 搜索候选与抓取结果分别标记 `candidate_unverified` / `retrieved_not_verified`；
- 默认只抓近期候选 URL，降低模型制造 URL 和网页 prompt injection 引发二次抓取的风险。

生产仍建议用出站防火墙/egress proxy 在网络层再次封锁内网和云 metadata；应用层防护不是唯一防线。

## 测试

普通测试完全离线，不需要私密材料或 API key：

```bash
uv run ruff check src tests
uv run pytest -m "not live"
```

冻结源码的离线结果：`132 passed, 2 deselected`（两条 live 测试被 `-m "not live"` 排除）。

真实烟测是显式 opt-in，不应作为普通 CI 必过条件：

```bash
RUN_LIVE_TESTS=1 uv run pytest tests/test_live_smoke.py -m live -vv

# 招投标专用 3+5 双向烟测
RUN_TENDER_LIVE_TESTS=1 uv run pytest tests/test_tender_live_smoke.py -m live -vv
```

冻结源码同时复跑通用+招投标专用两条公开官网烟测：`2 passed, 132 deselected in 39.96s`。详细历史取证记录见 [`docs/真实烟测记录_2026-08-15.md`](docs/真实烟测记录_2026-08-15.md)。live 测试是显式 opt-in，不作为普通 CI 必过条件。

测试覆盖：危险协议/端口/userinfo、域名/DNS/私网边界、重定向降级、robots 路径/最长匹配/fail-closed、非文本 MIME、动态壳与超长截断、候选来源、固定 3+5、正反阶段时序、未抓唯一候选、身份同页绑定、字段边界、风险词变体、人工闸门、DeepSeek 参数校验和 MCP 客户端调用。

## DeepSeek 与平台接入

DeepSeek API 本身不会执行 MCP。两种接法：

1. 平台已经有 MCP Host：启动本 stdio server，使用 `tools/list` / `tools/call`；
2. 平台只有 DeepSeek Function Calling：使用 `official_search_mcp.deepseek_adapter` 导出的 schema 与 dispatcher。招投标案件必须由宿主确定性调用 `crosscheck_tender_information`，不用 `tool_choice="auto"` 赌模型是否走完流程。

每个案件新建一个 `OfficialSearchService`，案件内完整复用，案件结束后销毁；这样可隔离进程内候选 URL 登记，不让不同案件串证据。

详细调用循环、错误处理、工具预算、候选 URL 来源和平台集成建议见 [`docs/接入DeepSeek与平台说明.md`](docs/接入DeepSeek与平台说明.md)。

## 文件结构

```text
official_search_mcp/
├── research/github_open_source_selection.md
├── docs/
│   ├── 接入DeepSeek与平台说明.md
│   ├── 用与不用的差别_大白话.md
│   ├── 招投标双向复核设计与追责边界.md
│   └── 真实烟测记录_2026-08-15.md
├── src/official_search_mcp/
│   ├── backends/                 # DDGS / SearXNG / Brave / fixture
│   ├── security.py               # URL、域名、DNS/IP 与连接时 pinning
│   ├── fetcher.py                # robots、重定向、流式大小限制
│   ├── extraction.py             # Trafilatura + 诚实降级
│   ├── tender.py                 # 固定 3+5 查询、字段观察与风险信号
│   ├── service.py                # 候选登记和四工具编排
│   ├── server.py                 # 官方 Python SDK MCPServer
│   └── deepseek_adapter.py       # Function Calling schema/dispatcher
├── tests/
├── .github/workflows/            # 离线 CI 与手动 live smoke
├── CONTRIBUTING.md
├── SECURITY.md
├── THIRD_PARTY_NOTICES.md
├── LICENSE
├── pyproject.toml
├── requirements.txt
└── uv.lock
```

## 尚未解决/有意不做

- DDGS 和搜索上游可能瞬时超时或无结果；生产应自管 SearXNG 或使用付费 Brave；
- 没有 JS 渲染，动态页面只返回降级，绝不声称正文完整；
- 不处理 PDF/OCR、登录页、验证码或反爬绕过；附件里才有的标包、金额、主体和签章是明确证据缺口；
- robots 规则用 Protego 0.6.2 的长度优先/通配匹配；无法解析或无法可靠执行的限制会 fail-closed，不自动降级放行；
- 通用工具的候选登记在进程内，重启后消失；招投标专用调用已用每次 `run_id` 独立候选表。分布式长期审计仍需平台维护案件级 approved URL 与原始工具 JSON；
- `run_id`、时间和哈希是单次输出的追溯字段，不是不可篡改长期审计库；请求人、复核人、审批人、时间、版本、取证原始 JSON 与签核意见必须由平台持久化并做访问控制/防篡改；
- 中标/成交公告只能支持“当时公布了该结果”的候选证据，不能推断合同已签、已履约、已验收或已付款；
- 搜索召回、主体真实性、公告时效、投标业绩归属和法律结论仍需人工/业务规则核验。

## 调研依据

完整 GitHub、发行版、许可证、issue、安全与上游文档比较见 [`research/github_open_source_selection.md`](research/github_open_source_selection.md)。

## 开源、贡献与安全报告

本项目以 [MIT License](LICENSE) 开源。依赖许可证与外部搜索服务边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，参与开发见 [CONTRIBUTING.md](CONTRIBUTING.md)。

SSRF、域名策略绕过、密钥泄漏或访问控制问题请不要先发公开 issue；请按 [SECURITY.md](SECURITY.md) 使用 GitHub 私密漏洞报告。
