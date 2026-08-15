# 接入 DeepSeek 与平台说明

## 结论先行

招投标案件不应让 DeepSeek 自由决定是否搜索、搜几次、是否做逆向复查。正确边界是：

```text
平台收集并确认结构化案件字段
        │
        ├─每个案件新建一个 OfficialSearchService
        │
        ├─宿主确定性调用 crosscheck_tender_information 一次
        │       └─服务端强制 F01–F03 完成后再跑 R01–R05
        │
        ├─平台原样持久化工具 JSON 与运行元数据
        │
        ├─DeepSeek 只解读已产生的证据台账，不改写 system_status/human_gate
        │
        └─实名人员核对原文、形成意见并签核
```

**不要用 `tool_choice="auto"` 赌模型会主动走完政府项目流程。** `auto` 允许模型直接生成文本，因此它不能成为“必定取证”的控制点。

DeepSeek API 本身也不是 MCP Host。Function Calling 只会生成工具调用意图；运行 Python/MCP、参数校验、网络请求、记录与重试都是平台责任。DeepSeek 官方文档也明确提醒，`function.arguments` 可能不是合法 JSON 或不满足 schema，调用前必须校验：[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)、[Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 四个工具的分工

| 工具 | 用途 | 政府项目是否可独立当“复核流程” |
|---|---|---:|
| `search_official_sources` | 单次官网搜索 | 否 |
| `fetch_official_page` | 读取本进程最近搜索得到的单页 | 否 |
| `search_and_fetch_official` | 通用搜索+最多 3 页读取 | 否 |
| `crosscheck_tender_information` | 招投标固定 3 正向+5 逆向+取页+台账+人工闸门 | **是，必用** |

前三个是调试或通用原子能力。招投标案件的平台流程必须指向第四个工具，否则无法证明逆向复查确实执行。

## 案件输入 schema

`project_name` 是 schema 必填项。工具在其他字段缺失时仍会搜索并留痕，但要达到最高的机械覆盖状态，`project_code`、`package_code`、`supplier_legal_name`、`unified_social_credit_code` 四项必须齐全；否则是 `insufficient_identifiers` 或更严格状态。

| 字段 | 含义 | 缺失主要风险 |
|---|---|---|
| `project_name` | 项目正式名称 | 必填，2–120 字符 |
| `project_code` | 项目/采购/招标编号 | 同名项目误合并 |
| `package_code` | 包号/标段号 | 同项目不同标包串证据 |
| `purchaser` | 采购人/招标人法定名称 | 主体归属不清 |
| `supplier_legal_name` | 供应商/投标人法定全称 | 简称、曾用名、母子公司混淆 |
| `unified_social_credit_code` | 18 位统一社会信用代码 | 同名法人不能稳定反绑 |
| `claimed_amount` | 申报金额原文 | 中标额/合同额/结算额口径混淆 |
| `claimed_date` | 申报日期原文 | 公告/成交/签约/验收日期混淆 |
| `region` | 行政区划或项目地 | 同名跨地区项目混淆 |

枚举值：

- `procurement_regime`: `government_procurement` / `mandatory_tender` / `unknown`；
- `notice_type`: `tender_notice` / `candidate_notice` / `award_result` / `contract_notice` / `acceptance_record` / `unknown`；
- `claimed_role`: `bidder` / `candidate` / `winner` / `contractor` / `completed_performance` / `unknown`。

平台不应让模型“猜”明明已有的字段。这些字段应来自用户确认的表单/案件记录；如需 DeepSeek 预抽取，必须先显示给操作人确认，再发起取证。

项目编号的纯数字部分少于 8 位会拒绝；可解析为 `YYYYMMDD` 的 8 位值也拒绝。其他纯数字编号只有在正文紧邻“项目/采购/招标编号（编码）”标签时才能作强锚点，避免把日期或流水号冒充项目编号。

MCP/DeepSeek 专用工具不暴露 `domains`、`max_results_per_query`、`max_pages_to_fetch`。它们不是模型输入：来源范围由服务器 allowlist/default domains 管控，服务端固定每查询最多 3 条、全案件最多取 10 页。只有受信操作者使用内部 service/CLI 时，才可在安全上限内收窄域名或调低预算；不得将这些参数转透传给模型。

## 服务端固定 3+5，不由模型编查询

| ID | 方向 | 目的 |
|---|---|---|
| F01 | 正向 | 按项目身份发现中标/成交/结果公告 |
| F02 | 正向 | 项目+供应商/采购人+标包交叉发现主体角色 |
| F03 | 正向 | 发现合同、履约验收/验收公告 |
| R01 | 逆向 | 更正、变更、澄清 |
| R02 | 逆向 | 终止、中止、暂停、废标、流标、取消、撤销/撤回、作废/废止、停止采购 |
| R03 | 逆向 | 质疑、投诉处理/决定、监督检查、中标/成交/采购结果无效 |
| R04 | 逆向 | 按法定主体/统一社会信用代码反绑项目与标包 |
| R05 | 逆向 | 后续合同/验收、合同解除/终止、验收不合格、重新采购/招标 |

实现会先完成 F01–F03，再完成 R01–R05，八条搜索全部返回后才取页，避免慢页挤掉逆向时间。正向与逆向分别保留发现来源；同 URL 在逆向发现时会以逆向 provenance 重取。输入值会去除搜索运算符注入并放入固定模板。

## 输出怎么读

### `system_status`

| 值 | 真实含义 | 不得解读为 |
|---|---|---|
| `two_pass_completed_pending_human` | 在当次预算/截止时间内，完整度条件满足，仍待人工 | 已核验、已通过 |
| `risk_signals_found` | 已抓取片段命中更正/终止/投诉等词 | 已证明违法或业绩无效 |
| `inconclusive` | 有部分线索，但失败、缺页、缺字段或覆盖不完整 | 没问题 |
| `no_candidate_observed` | 当次后端/预算/截止时间内未观察到候选 | 事实不存在 |
| `insufficient_identifiers` | 项目编号、标包、供应商法定名称、信用代码任一缺失 | 核验失败/通过 |
| `offline_fixture_only` | 至少部分不是实时联网搜索 | 已联网取证 |

### `coverage`

`required_forward_queries=3` 和 `required_reverse_queries=5` 是应执行数；`successful_*` 是成功返回的查询数。还要一起看：

- 候选数、取页尝试数、唯一取页 URL 数、`unfetched_candidate_count`；正反向重复 URL 不得掩盖未抓唯一候选；
- 完整取页、动态/空正文/超长截断数、`identity_coherent_page_count`、`key_binding_page_count`；
- 不同官网 host 与不同正文哈希数；
- 重复正文哈希组（转载或镜像不能重复计票）；
- `distinct_search_backends` 与 `different_reverse_backend_configured`；后者只说后端名不同，不证明官方来源独立；
- `live_search_performed`；
- `budget_exhausted`（超出取页预算的候选是明确缺口）。
- `search_result_limit_hit_query_ids`；任一查询已收到溢出候选或命中上游返回上限，都不允许进入最高机械覆盖状态。

### `field_observations` 和 `risk_signals`

`observed_in_retrieved_excerpt` 只能由“同页绑定项目名+项目编号”的完整可读页面支持；跨无关页拼字段不计。项目编号/信用代码用标识符边界，标包/金额用文本与数字边界，日期用日期边界，其他字段用 NFKC+字母数字子串。即使命中，也不理解主体角色或金额口径。

最高机械覆盖状态还要求至少一个唯一完整页在同页绑定项目名、项目编号、申报标包、供应商法定名和信用代码；不允许用包1页的金额和包2页的供应商拼成一条证据链。“某有限公司分公司/子公司”也不当申报法人全称精确命中。

`risk_signals` 是“文本指示需人工查”，不是自动法律定性。扫描会做 NFKC 与空白规范化，并用受限模式覆盖“结果公告无效”、“投诉决定”、“合同被解除”、“验收结果不合格”等插词变体。只有同页还命中项目编号或名称锚点才输出；仍必须打开 `evidence_id`/URL 核对标包、公告类型、时间与对象。

### `human_gate`

固定输出：

- `manual_review_required=true`；
- `system_may_auto_approve=false`；
- `platform_must_record_reviewer_identity=true`；
- 人工结果只能是 `approved_by_human` / `rejected_by_human` / `needs_more_evidence`。

平台不得用 DeepSeek 的文本结论覆盖这个闸门，不得把 `ok=true` 解读为业务通过。`ok` 只表示工具有可返回的流程数据；正反查询全部失败时会 `ok=false`。

## 接法 A：平台已有 MCP Host

### stdio 配置

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

本地 stdio 不开放监听端口，适合 MVP。若未来改成 Streamable HTTP，必须增加认证、授权、租户/案件隔离、请求体限制、出站网络隔离和反向代理；不要把无认证 MCP 暴露到公网。

宿主应对每个案件建立独立 MCP server/service 生命周期。若长驻 server 暂时无法按案件隔离，平台至少要维护租户+案件级 approved URL 集合，不要用 `OFFICIAL_SEARCH_ALLOW_DIRECT_FETCH=true` 作替代。

## 接法 B：平台只有 DeepSeek Function Calling

项目提供：

```python
from official_search_mcp.deepseek_adapter import (
    deepseek_tool_definitions,
    dispatch_deepseek_function,
)
from official_search_mcp.service import OfficialSearchService
```

- `deepseek_tool_definitions()` 返回 4 个 OpenAI-compatible function schema；
- `dispatch_deepseek_function()` 限制参数 UTF-8 为 16 KiB，校验 JSON object，拒绝未知工具/额外字段，并设置宿主 deadline；专用流程未显式传值时默认为服务器全流程超时+15 秒的收尾余量；
- dispatcher 和 MCP server 共用同一套 `OfficialSearchService` 业务逻辑。

### 推荐：宿主直接执行，DeepSeek 后解读

```python
import json

from official_search_mcp.deepseek_adapter import dispatch_deepseek_function
from official_search_mcp.service import OfficialSearchService


async def collect_case_evidence(approved_case: dict) -> dict:
    # 每个案件新建；不和其他案件复用候选 URL 登记。
    service = OfficialSearchService()
    result_json = await dispatch_deepseek_function(
        service,
        "crosscheck_tender_information",
        json.dumps(approved_case, ensure_ascii=False),
    )
    result = json.loads(result_json)
    # ok=true 也不是通过；必须原样入台账并进入人工闸门。
    persist_original_tool_json(result_json)
    return result
```

这种方式不需要先问模型“要不要调工具”。DeepSeek 只在取证完成后收到结构化结果，作为操作员辅助摘要层。平台应明确标注工具 JSON 内的网页正文是不可信数据，不得把它拼入 system prompt，也不执行正文内的命令或 URL。

### 如必须用 DeepSeek 原生 tool loop

宿主应强制指定工具，不用 `auto`：

```python
forced_choice = {
    "type": "function",
    "function": {"name": "crosscheck_tender_information"},
}

response = await client.chat.completions.create(
    model=model_name_from_environment,
    messages=messages,
    tools=[
        tool for tool in deepseek_tool_definitions()
        if tool["function"]["name"] == "crosscheck_tender_information"
    ],
    tool_choice=forced_choice,
)
```

之后必须确认：`finish_reason == "tool_calls"`、只有一个调用、名称正确、参数与操作人确认的案件值一致；有任何不一致就拒绝执行。工具结果必须以对应 `tool_call_id` 的 `role="tool"` 回填。

DeepSeek strict 模式是 Beta，对 JSON Schema 子集有特殊约束；当前套件的 Pydantic schema 含可选字段/长度约束，不应在没有独立兼容测试时直接加 `strict=true`。无论 API strict 是否开启，本地 Pydantic 校验仍是必选边界。

## Thinking 模式：`reasoning_content` 与 `finish_reason` 边界

DeepSeek [Thinking Mode 官方文档](https://api-docs.deepseek.com/guides/thinking_mode) 规定：当某个 assistant 消息包含 tool calls 时，该消息的 `reasoning_content` 必须完整放回后续 API 请求，否则可能返回 400。最稳妥的做法是把 SDK 返回的整个 assistant message 原样 append，再 append `role=tool`。

`reasoning_content` 是模型调用的上下文运输状态，**不是官方证据、不是审批意见、不能代替实名人员的可复核理由**。是否长期保留原始 reasoning 应遵循平台的数据分类、保密和保留期策略；审计主依据仍是工具输入/输出、原网页、哈希、版本、失败记录与人工签核。

对 `finish_reason` 必须显式分支：

| `finish_reason` | 平台处理 |
|---|---|
| `tool_calls` | 只执行结构化 `tool_calls`，严格验名称/JSON/schema/案件值；回填结果后继续 |
| `stop` | 可作为模型摘要结束；仍不是业务审批 |
| `length` | 输出可能被截断；标记未完成，不保存为最终结论 |
| `content_filter` | 内容被过滤；标记未完成并交人工 |
| `insufficient_system_resource` | 推理资源不足导致中断；按平台重试策略处理，不产生结论 |
| `null`（流式中间块） | 继续接收，不提前执行/审批 |

## 每个案件新建 `OfficialSearchService`

安全默认下，只能抓取同一服务实例最近搜索登记的候选 URL。因此生命周期应为：

1. 新案件 → 新建 service；
2. 该案件的一次 `crosscheck_tender_information` 内复用该 service；
3. 原始输入/输出入平台长期台账；
4. 案件完成后销毁 service，不让下一案件继承候选登记。

“同一案件内复用”和“不同案件不复用”并不矛盾。前者保证 search→fetch 来源链，后者防止跨案件串证据。

## 生产正向/逆向搜索后端

```bash
export OFFICIAL_SEARCH_BACKEND=searxng
export OFFICIAL_SEARCH_REVERSE_BACKEND=brave
export OFFICIAL_SEARCH_SEARXNG_URL='http://127.0.0.1:8080'
export OFFICIAL_SEARCH_BRAVE_API_KEY='仅从密钥管理/运行环境注入'
export OFFICIAL_SEARCH_DEFAULT_DOMAINS='gov.cn'
export OFFICIAL_SEARCH_TENDER_TIMEOUT_SECONDS=150
```

正向自管 SearXNG+逆向 Brave 可降低单一索引的共同漏检，但仍不能保证全量收录，也不能称为“独立官方证据”。无密钥选项是 `searxng` + `ddgs`，DDGS 无 SLA，适合原型/备用而不是默认生产依赖。逆向后端未配置时复用正向后端，`coverage.different_reverse_backend_configured=false`会明确暴露这一配置。即使该值为 `true`，两个后端也可能依赖重叠索引。

Key 不得放入 MCP JSON、工具参数、Git、日志或截图。`official-search-cli config` 只显示是否配置，不显示值。

## 政府采购与依法必须招标要分线

`procurement_regime` 不是装饰字段。两条制度线的发布媒介、公告名称、监管部门和后续记录不完全相同，不能因为都出现“招标/中标”就混成一条。

### `government_procurement`

参考官方文件：

- [财政部令第 101 号《政府采购信息发布管理办法》](https://tfs.mof.gov.cn/caizhengbuling/201912/t20191210_3439068.htm)：政府采购项目信息包括公开招标、资格预审、单一来源、中标/成交结果、合同等，也包括投诉处理、监督检查等监管信息；并规定中国政府采购网/省级分网的发布与信息一致性责任。
- [财政部令第 87 号《政府采购货物和服务招标投标管理办法》](https://www.mof.gov.cn/gp/xxgkml/tfs/201707/t20170718_2652766.htm)：适用于政府采购货物和服务招投标，覆盖采购需求、评标、中标、合同与内控等环节。

搜索优先顺序、同一信息冲突处理和当地分网选择必须按项目属地与当时有效规定由业务/法务确认，MCP 不自动做法律适用结论。

### `mandatory_tender`

参考官方文件：

- [国家发展改革委令第 10 号《招标公告和公示信息发布管理办法》](https://zfxxgk.ndrc.gov.cn/web/iteminfo.jsp?id=18519)：适用于招标项目的资格预审公告、招标公告、中标候选人公示、中标结果公示等，并对依法必须招标项目的公开发布和全国汇总作出规定。

“中标候选人公示”不是“中标结果”，而中标结果也不是合同、履约、验收或付款证明。

当 `procurement_regime=unknown` 时，报告会明确要求人工判断制度线，平台不得悄悄默认为其中一条。

## URL、PDF、动态页和“中标≠履约”

- 搜索结果是 `candidate_unverified`；有 URL 只能证明“有这个候选地址”。
- 抓取成功是 `retrieved_not_verified`；哈希标识当次取得的字节，不证明业务事实。`content_truncated=true` 表示只返回前 8,000 字符，必须补读未展示部分，不计完整取页。
- 当前不处理 PDF/OCR，不执行 JavaScript；关键附件/动态页读不到就是证据缺口，不能由模型补写。
- 中标/成交公告只表示当时公布的结果；要支持 `completed_performance`，至少应对合同主体、标包、合同金额、履约/验收记录与后续更正做人工连续核对。

## 错误处理

所有工具都返回统一 envelope：

```json
{
  "schema_version": "1.0",
  "ok": false,
  "data": null,
  "error": {
    "code": "robots_disallowed",
    "message": "robots.txt 不允许该自动客户端访问此 URL。",
    "retryable": false,
    "details": {}
  },
  "warnings": []
}
```

平台按 `code` 处理，不匹配中文 message：

| 类型 | 示例 code | 建议处理 |
|---|---|---|
| 参数/授权 | `invalid_tender_claim`、`domain_not_authorized` | 不重试；要求操作人修正 |
| 来源保护 | `url_not_in_search_candidates` | 不让模型改个 URL 继续试 |
| SSRF/重定向 | `ssrf_blocked_address`、`https_downgrade_rejected` | 不重试，记安全事件 |
| robots/访问限制 | `robots_disallowed`、`access_blocked` | 记证据缺口，禁止绕过 |
| 临时搜索后端 | `search_timeout`、`search_backend_rate_limited` | 仅在宿主预算内重试一次/切受控后端 |
| 页面内容 | `unsupported_content_type`、dynamic warning | 显示 PDF/JS 缺口，不生成替代正文 |
| 双向全流程 | `all_tender_searches_failed`、`tender_workflow_timeout` | 本案件不产生可采信结论，记失败台账 |

“逆向查询 0 结果”不是错误码，但也不等于“不存在更正/投诉”；它会写入 `review_reasons`。

## 长期审计台账由平台实现

MCP 给出 `run_id`、`server_version`、`workflow_version`、`raw_input_sha256`、`normalized_input_sha256`、`plan_sha256`、`started_at`、`cutoff_at`、`completed_at`、查询、URL、正文哈希与失败信息，但它不是不可篡改长期审计系统。平台至少要持久化：

- 案件 ID、租户、请求人、操作人、复核人、审批人及职责分离；
- 操作人确认后的原始输入、完整工具 JSON、schema/workflow/MCP 版本；
- DeepSeek 模型 ID、API 请求 ID、`system_fingerprint`、`finish_reason`、工具调用 ID；
- 搜索后端、查询时间、有效 query、原/规范/最终 URL、重定向链、取页时间、正文 SHA-256；
- 所有超时、robots、403/429、非文本、动态页、预算耗尽和重试记录；
- 人工打开过哪些原页、对比哪些字段、形成什么意见、附件/截图/签章证据、签核时间；
- 后续更正定期复查计划、证据保留期、权限、防篡改和导出策略。

不要只存 DeepSeek 最后一段自然语言；那是摘要，不是取证链。

## 最小接入验收

1. `tools/list` 能看到 4 个工具及 output schema。
2. 新案件必须新建 service，不同案件候选 URL 不能互用。
3. 宿主确定性调用 `crosscheck_tender_information`，不依赖 `tool_choice="auto"`。
4. 输出能看到 F01–F03 和 R01–R05，逆向确实在正向后执行。
5. 列举所有 `system_status` 时，UI 都仍显示“待人工复核”，不出现自动 pass。
6. `coverage`、`field_observations`、`risk_signals`、`review_reasons`、`human_gate` 均原样展示/入库。
7. fixture 显示 `live_search_performed=false` 且状态为 `offline_fixture_only`，不用于生产结论。
8. PDF、JS 动态壳、robots、403、超时和预算耗尽都显示为证据缺口。
9. `finish_reason=length/content_filter/insufficient_system_resource` 不会形成最终结论；thinking tool call 的 `reasoning_content` 能按官方要求回传。
10. 日志、错误、前端网络请求不含 DeepSeek/Brave key 和整页正文。
11. 平台可记录复核人身份、人工意见、签核时间和完整原始工具结果。
12. 现场只说“自动正向搜索并逆向排查公开官网线索，供人工核验”，不说“系统保证业绩真实”。
