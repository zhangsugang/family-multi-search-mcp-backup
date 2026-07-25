# Family Multi-Search MCP

本目录是八源多源搜索运行时。它同时提供本地 MCP stdio、受家庭 Key 保护的 MCP Streamable HTTP 和 REST，保留本机私有登录态，不把 Cookie、Profile、API Key、家庭 Key 或个人记录写入仓库。

## 搜索来源

| 来源 | 主要职责 | 运行方式 | 实测 active limit |
| --- | --- | --- | ---: |
| 豆包 | 抖音、头条、团购、现场体验与国内消费信号 | 豆包桌面版 CDP，每任务独立 Page | 10 |
| 元宝 | 微信公众号、视频号和腾讯生态 | 每任务独立 BrowserContext | 10 |
| 文心 | 百度、政府网页和国内通用网页 | 每任务独立 BrowserContext | 20 |
| Tavily | 全球网页和直接网页结果 | 异步 HTTP API | API 限流控制 |
| Exa | 语义搜索、权威长文和历史资料 | 异步 Adapter | Adapter 限流控制 |
| Gemini | YouTube、海外视频和英文社区资料 | 隔离 Profile + CDP，每任务独立 Page | 10 |
| Grok | X/Twitter 实时舆情和公开原帖 | 隔离 Profile + CDP，每任务独立 Page | 20 |
| 千问 | 淘宝、飞猪、高德、饿了么公开信息发现 | 隔离 Profile + CDP，每任务独立 Page | 5 |

所有浏览器来源都暴露 20 个逻辑槽位，但只允许实测通过的 `active_limit` 同时执行。每个任务结束后销毁 Page 或 BrowserContext，不能继承其他家庭成员的对话历史。

## MCP 工具

- `doubao_search`
- `tavily_search`
- `yuanbao_search`
- `exa_search`
- `wenxin_search`
- `gemini_search`
- `grok_search`
- `qianwen_search`
- `research_round`
- `search_all`
- `search_status`

`research_round` 和 `search_all` 会为八个来源同时生成：

- `general`：公开网络、官方原文和独立报道。
- `specialized`：各平台擅长的生态定向查询。

地点、园区和文创项目会自动延伸基础信息、地址交通、开放时间、旅游攻略、团购活动、客流热度、投资运营、运营主体、法人、实际控制人、招商业态和风险争议。可选延伸维度最多 8 个。

## 千问只读边界

家庭共享千问仅用于公开信息发现：

- 允许公开商品价格、飞猪旅游产品、高德路线、饿了么餐饮和本地生活研究。
- 禁止下单、预订、支付、充值、退款、提现、转账、领券核销或账户变更。
- 禁止查询本人或第三人的支付宝、社保、公积金、税务、身份、护照、车辆、不动产或其他个人官方记录。
- 政策、法规、规则和公开制度研究可以执行，但“公开”不能作为个人记录查询的绕过词。
- 未来若增加个人服务，必须采用逐用户授权、权限隔离和每次显式确认，不能复用当前家庭共享入口。

## 私有运行状态

默认安装目录：

```text
~/.zcode/mcp/multi-search-mcp/
├── tools/
├── config/
├── scripts/
└── private/
```

`private/` 仅说明目录用途，不应读取、打印、打包或提交其内容。常见状态位置：

- `private/search-mcp/config.json`
- `private/search-mcp/family-keys.json`（仅保存加盐哈希）
- `private/search-mcp/family-key-handoff.json`（一次性安全交接，`0600`）
- `private/yuanbao/storage-state.json`
- `private/wenxin/storage-state.json`
- `private/gemini/chrome-profile/`
- `private/grok/runtime-profile/`
- `private/qianwen/runtime-profile/`

所有 CDP 端点固定绑定环回地址：

- 豆包：`127.0.0.1:9333`
- Grok：`127.0.0.1:9555`
- Gemini：`127.0.0.1:9556`
- 千问：`127.0.0.1:9557`

不得把这些端口暴露到局域网或公网。

## 安装与更新

部署脚本只同步公开的 `tools/`、`config/` 和公共脚本，不会对安装根目录执行 `rsync --delete`，也不会删除或覆盖 `private/`：

```bash
bash scripts/deploy-local.sh
```

部署前会运行完整测试，部署后会通过 MCP stdio 执行 `initialize` 和 `tools/list`，验证 11 个公开工具。

远程服务部署：

```bash
MULTI_SEARCH_KEY='<临时从安全交接文件读取>' bash scripts/deploy-remote.sh
```

远程进程由用户 LaunchAgent `com.bri-king.family-multi-search` 管理，只监听 `127.0.0.1:8765` 且固定一个 worker。公网由现有 Cloudflare Tunnel 映射：

- MCP：`https://mcp-search.bri-king.com/mcp`
- REST：`https://mcp-search.bri-king.com/v1`
- 公共极简健康检查：`https://mcp-search.bri-king.com/healthz`

远程 MCP 工具为 `search_once`、`research`、`continue_research`、`get_research_result` 和 `provider_status`。所有 `/mcp` 与 `/v1/*` 请求都需要家庭 Bearer Key。每个 Key 最多自动绑定 10 个公网 IP，地址仅以不可逆摘要存储；请求并发、任务所有权和“一个未完成研究”限制按绑定地址隔离，而不是压在整个 Key 上。五个及以上地址可以同时提交完整研究并立即获得 `request_id`；固定两个 worker 同时运行完整八源研究，其余任务保持 `queued` 并按 FIFO 自动执行，不再使用旧的 30 秒 semaphore 排队超时。

临时目录验证：

```bash
root="$(mktemp -d /tmp/family-multi-search-install.XXXXXX)"
mkdir -m 700 "$root/private"
MULTI_SEARCH_INSTALL_ROOT="$root" bash scripts/deploy-local.sh
```

## 测试

```bash
python3 -m pytest tests -q
python3 tests/test_mcp_stdio.py --runtime tools/multi_search_mcp.py
```

当前完整测试结果：`159 passed`。Python 3.14 下的警告来自第三方 `pytest_asyncio` 弃用接口，不是产品测试失败。

本地已部署网关的 20 个认证客户端状态探针结果：20/20 完成，墙钟 0.945 秒，p50 0.506 秒，p95 0.599 秒。公网 Cloudflare 路径的同类探针为 20/20 完成，墙钟 3.145 秒，p50 2.076 秒，p95 2.836 秒。该结果验证鉴权、协议与多客户端接入，不代表 20 个完整八源研究同时执行；完整研究仍由全局 2 槽位公平限流。

## 分级并发探针

探针按来源依次运行 2、5、10、20 级测试。每个任务使用唯一标记，并验证：

1. 回答包含自身标记。
2. 回答不包含其他任务标记。
3. 每个任务拥有不同会话 URL。
4. 所有任务均完成。

```bash
python3 scripts/live_concurrency_probe.py \
  --provider doubao \
  --levels 2,5,10,20 \
  --query "1970文创园"
```

输出仅写入 `/tmp/family-multi-search-probes`，不会写入仓库 `output/`。输出不保存完整回答、Cookie、Profile 或授权头。

## 实测容量

| 来源 | 最高通过等级 | 墙钟时间 | p50 | p95 | 首个失败等级 |
| --- | ---: | ---: | ---: | ---: | --- |
| 豆包 | 10 | 50.828s | 40.170s | 50.825s | 20：4/20 完成 |
| 元宝 | 10 | 60.506s | 54.016s | 60.506s | 20：17/20 完成 |
| 文心 | 20 | 26.079s | 23.918s | 26.007s | 无 |
| Grok | 20 | 148.570s | 96.588s | 148.541s | 无 |
| Gemini | 10 | 40.371s | 32.883s | 40.371s | 20：13/20 完成，14 个独立 URL |
| 千问 | 5 | 30.606s | 20.902s | 30.606s | 10：10/10 有结果，但 5 个超时前未返回自身标记 |

失败等级不会启用为默认并发上限。

## 八源双通道实测

对“1970文创园”执行一次八源双通道研究：

- 墙钟时间：60.196 秒。
- 豆包、元宝、文心、Tavily、Exa、Grok、千问的两条通道均完成。
- Gemini 在该次全源联合运行中两条通道均失败；Gemini 独立分级测试的 10 并发通过，因此联合资源竞争或页面状态仍需继续监控。
- 共保留 94 条唯一引用。
- `团购活动` 和 `客流热度` 获得直接覆盖；地址交通、开放时间和旅游攻略仍存在证据缺口，调用方不得把自动延伸维度误写成已证实事实。

## 已知边界

- 搜索平台可能变更 DOM、触发登录失效、验证码或账号风控；系统不会绕过这些限制。
- 模型回答属于平台观察，事实应以直接引用和官方原文为准。
- 播放量、点赞量和搜索热度不能直接视为客流、销量、订单或营业额。
- `active_limit` 是本机、本账号和本次网络环境下的实测值，不是平台永久承诺。
- 远程网关、家庭 Key、Cloudflare ingress 和 ZCode/WorkBuddy Skill 已实现；公网可用性仍依赖家庭 Mac、Tunnel 和外部平台正常运行。
