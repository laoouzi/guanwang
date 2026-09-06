# laoban（老板）— AI 员工公司经营平台 · AI 协作开发上下文说明

> 本文档面向 AI 编码助手（Codex / Trae / Claude Code / Cursor 等），一次性讲清
> 这是什么项目、怎么跑起来、代码在哪、改哪里要注意什么。

---

## 1. 一句话定位

**laoban 是一个开源、自托管的多 Agent 编排框架**：把一支「AI 员工 + 人类员工」
混合团队当作一家真实公司来经营——招聘、入职、派单、执行、评审、驳回、考核、
晋升、记分，组织随业务自动生长。老板通过 CLI 或 Web 看板（「老板驾驶舱」）
管理一切。

核心命题：**AI 员工与人类员工同在一个部门树里协作**（`kind=ai / human` 共用
部门 / 汇报链 / 绩效体系），人类员工每天有自己的任务清单与收件箱，AI 超出能力
时把活派回给人类，人类完成后流程自动恢复。

## 2. 技术栈与约束（先读这个）

| 项 | 内容 |
|---|---|
| 语言 | Python 3.10+，**标准库优先**，运行时零第三方依赖 |
| 可选依赖 | `cryptography`（飞书事件加密 + Web Push 加密，缺失时优雅降级）；`pytest`（测试）；`playwright`（E2E 视觉检查） |
| 存储 | 本地 JSON 文件（`JsonStore`，原子写 `os.replace`），无数据库 |
| Web 服务 | 标准库 `http.server.ThreadingHTTPServer`，无 Flask/FastAPI |
| 前端 | 单文件 `dashboard.html`（原生 JS，无构建步骤、无框架） |
| LLM | OpenAI 兼容网关（DeepSeek / 千问 / GPT / Ollama），无 Key 时回退 MockLLM 演示模式 |
| 测试 | `pytest tests/` —— **482+ 项全绿**（含 IM 渠道、看板操作、RBAC、状态机等） |

**开发铁律**：改动不引入新的硬依赖；Web 层保持标准库；前端保持单文件零构建。

## 3. 目录结构（改代码前先定位）

```
laoban/
├── cli.py                 # 入口：全部子命令（laoban <cmd>）
├── org.py                 # 组织配置加载/instantiate（org.json → 员工档案）
├── bootstrap.py           # 启动模式：四元老（HR/法务/IT/财务）产出组织设计
├── recruitment.py         # 双轨招聘：编制申请 → 审批 → 入职
├── demo.py                # laoban demo：MockLLM 全流程演示（无需 API Key）
├── core/                  # ★ 制度内核（纯逻辑，无 IO 依赖之外的东西）
│   ├── task.py            #   Task 数据类 + 状态常量
│   ├── state_machine.py   #   任务状态机：pending→triage→planning→review⇄驳回(≤3轮)→assigned→doing⇄waiting_human→reporting→done；blocked
│   ├── employee.py        #   Employee 数据类（人机统一：kind=ai/human）
│   ├── store.py           #   JsonStore（tasks/ employees/ 目录 + 原子写 + 员工读写锁）
│   ├── workstation.py     #   工位队列：assign_task_auto 派发入队 / enqueue / dequeue
│   ├── messenger.py       #   点对点消息总线（唯一事实源）+ 通知钩子 set_notifier + suppress_notify
│   ├── human_inbox.py     #   人类待办收件箱（AI 派给人类的结构化待办 + 人→人闭环）
│   ├── permission.py      #   权限矩阵校验（协作/工具/支出限额/自主等级）
│   ├── points.py          #   积分与 ROI（验收+10×(评分/5)、驳回-5、时效奖惩）
│   ├── ledger.py          #   绩效账本（完成数/成本/驳回率/人类介入率，落 ledger.json）
│   ├── finance.py         #   CFO 周报（按周归档：成本/积分/ROI/预算建议）
│   ├── promotion.py       #   晋升：AI 积分升自主等级 / 人类年度评估升管理
│   ├── retro.py           #   AI 复盘（低分/空评语 → LLM 生成教训 → 回写记忆）
│   ├── feedback.py        #   经验回写
│   ├── directory.py       #   组织通讯录（AI prompt 中可见的同事视角）
│   ├── scheduler.py / dispatcher.py   # 调度/派单
│   ├── lifecycle.py       #   员工生命周期（suspend/activate/terminate；terminate 不可逆）
│   ├── memory.py          #   记忆结构
│   └── auth.py            #   口令鉴权（PBKDF2；设过任一口令即启用登录）
├── runner/                # ★ 执行引擎
│   ├── runner.py          #   单任务执行（LLM 工具循环 [TOOL] 协议）
│   ├── worker.py          #   WorkerLoop：后台常驻扫描队列自动执行
│   ├── tools.py / collab_tools.py   # 工具集（send_message/delegate_task/python_exec/file_write…）
│   ├── guard.py           #   安全 Guard（路径越权/命令黑名单等）
│   ├── reviewer.py        #   评审员（准奏/封驳）
│   ├── approval_queue.py / approval_log.py   # 审批队列与审批日志
│   └── chat.py            #   看板人↔AI 聊天
├── llm/
│   ├── gateway.py         #   多供应商网关（provider 注册与路由）
│   ├── openai_compatible.py  # OpenAI 兼容实现（环境变量发现）
│   └── mock.py            #   MockLLM（演示/测试）
├── im/                    # ★ IM 渠道层（多渠道抽象）
│   ├── channel.py         #   IMChannel 抽象基类 + SenderChannel 适配器
│   ├── hub.py             #   ChannelHub：渠道注册/入站分发/出站多渠道路由
│   ├── feishu.py          #   飞书（唯一真实实现：事件回调+消息 API+加密）
│   ├── wecom.py           #   企业微信骨架桩（凭证就绪填 TODO 即接入）
│   ├── dingtalk.py        #   钉钉骨架桩（同上）
│   ├── binding.py         #   绑定表 im_bindings.json：IM 账号 ↔ 员工 id
│   ├── router.py          #   入站消息路由（「同事id: 内容」格式）
│   ├── notify.py          #   MessageNotifier：新消息 → IM 摘要 + Web Push 双通道
│   └── orgsync.py         #   组织同步：OrgSyncSource 抽象 + 增量融合 + 轮询
├── dashboard/             # ★ Web 看板（老板驾驶舱）
│   ├── server.py          #   HTTP 服务 + 全部 API 路由 + UrgeCenter 催办中枢
│   ├── dashboard.html     #   单文件前端（账房美学：台账/朱批/墨线）
│   ├── rbac.py            #   视图权限（admin 全量/manager 本部门/staff 本人+脱敏）
│   ├── webpush.py         #   Web Push：VAPID + RFC8188 aes128gcm 加密推送
│   ├── sw.js / manifest.json / icon-*.png   # PWA（可安装+离线壳+推送）
├── templates/default_org.json   # 默认组织模板（6 部门 10 岗位）
└── acceptance.py          # 验收套件（3 类任务自动判定）

tests/                     # pytest 全量（46 个文件 7500+ 行）
scripts/mobile_view_check.py  # Playwright E2E 视觉检查（375×812 真机视口）
```

## 4. 数据模型（三个核心实体）

```python
Task:      id/title/state/instruction/due_at/assignee/plan_horizon/
           flow_log[](每次流转:at/from/to/actor/remark) + urge_log[](每次催办) +
           progress_log[](交付物:cost/elapsed) + review_round/block_reason

Employee:  id/name/kind(ai|human)/department/reports_to/status(active|suspended|terminated)/
           job_description/permissions{role,autonomy_level,spending_limit,can_assign_human_tasks,…
           }/compensation{salary_monthly|cost_per_1k_tokens}/memory{experiences[],notes[]}/
           workspace{queue[]}
Message:   id/from/to/content/task_id/created_at/read_at
```

存储布局（`--root` 指向的目录，默认 `.laoban/`）：

```
.laoban/
├── tasks/*.json           # 一任务一文件
├── employees/*.json       # 一员工一文件
├── messages/*.json        # 一消息一文件（消息总线 = 唯一事实源）
├── ledger.json            # 绩效账本
├── auth.json              # 口令（PBKDF2）
├── im_bindings.json       # IM 账号 ↔ 员工绑定
├── org.json               # 组织配置（可选，缺省用内置模板）
├── vapid.json / webpush_subs.json   # Web Push 密钥与订阅
└── approvals/ …           # 审批日志等
```

## 5. 关键机制（改代码容易踩的点）

### 5.1 消息总线是唯一事实源
`core/messenger.py` 落盘消息后触发进程级钩子 `_notifier`（看板启动时注入
`MessageNotifier` → IM 摘要 + Web Push 双通道推送）。**推送失败绝不影响落盘**。
两条路径会抑制钩子（`suppress_notify`）防双推：IM 渠道线程处理入站、
UrgeCenter 发催办信（催办自带定向推送）。

### 5.2 IM 渠道抽象（laoban/im/）
所有渠道实现 `IMChannel` 接口（send_text / send_text_chat / handle /
fetch_departments / fetch_users），注册进 `ChannelHub`。上层只认 hub：
- **出站**：`hub.push_employee(emp_id, text)` 按绑定表路由到所有已绑定渠道；
- **入站**：`POST /api/im/webhook/<platform>` 统一入口；
- **新增渠道** = 实现 IMChannel + 注册，零上层改动。
- 飞书是唯一真实实现；企微/钉钉是**故意留的骨架桩**（TODO 注释写明接入步骤
  与所需环境变量），凭证就绪填 TODO 即接入，别当成 bug。

### 5.3 组织同步（im/orgsync.py）
`OrgSyncSource` 抽象（fetch_departments/fetch_members 返回标准化结构）→
`sync_org` 增量融合进 store。铁律：**只动人类员工，绝不覆盖 AI 员工**；匹配
优先级 = 已有 IM 绑定 → 姓名；默认不新建（`sync_create=True` 才拉新人）。
轮询由 `OrgSyncSweeper` 定时跑，`POST /api/org/sync` 手动触发（仅 admin）。

### 5.4 员工并发安全
看板是多线程 HTTP + worker 后台线程。所有跨线程员工字段变更必须走
`store.update_employee(emp_id, fn)`（锁内重载→改→存）。**禁止**在 fn 里
做 LLM/网络调用（长时间持锁）。

### 5.5 状态机与催办
任务流转唯一入口 `core/state_machine.advance(task, to, actor, remark)`，非法
转移抛 `IllegalTransition`。催办中枢 `UrgeCenter`（dashboard/server.py）：
超期 → 站内信必达 + IM 同步推送 + 连续未响应沿 reports_to 链升级抄送上级。
手动催（API）与自动催（_AutoUrgeSweeper）共用同一判定，口径一致。

### 5.6 RBAC 三角色
`dashboard/rbac.py`：`role_of()` = 显式 permissions.role > 有人向他汇报（manager）
> staff。admin 全量；manager 本部门；staff 本人（敏感字段 permissions/memory/
model_config 脱敏）。所有看板 API 都要过 `_require_view` / 角色守卫。

### 5.7 Web Push（dashboard/webpush.py）
VAPID P-256 密钥对（{root}/vapid.json 长期复用）+ RFC8188 aes128gcm 加密
（实现与 http_ece 参考对齐，可用 RFC 派生步骤独立验证）。缺 cryptography 时
`enabled=False` 静默降级。订阅 API：`POST /api/push/subscribe` /
`api/push/unsubscribe`（只能订自己的），公钥 `GET /api/push/vapid`。
注意：本地 HTTP 下浏览器推送不可用（需 HTTPS），订阅流程仅服务端可测。

## 6. 运行与验证

```bash
pip install -e .                      # 或直接 python -m laoban
pip install cryptography pytest        # 可选依赖

laoban demo                           # 演示模式：无 Key 跑通全流程（含人机协作）
laoban dashboard --port 7891          # Web 看板（无 Key → MockLLM 演示模式）
python -m laoban dashboard --root .laoban --port 7891 --no-worker

pytest tests/ -q                      # 全量测试（应全绿）
python scripts/mobile_view_check.py   # Playwright E2E（需 playwright + chromium）
```

看板鉴权：设过任何员工口令（`laoban auth passwd --who <id>`）即启用登录，
否则免鉴权（老板全量视角）。

环境变量（部分）：
`LAOBAN_FEISHU_APP_ID/APP_SECRET`（飞书）、`LAOBAN_WECOM_*` / 
`LAOBAN_DINGTALK_*`（桩渠道凭证）、`LAOBAN_AUTO_URGE=0`（关自动催办）、
`LAOBAN_ORG_SYNC=0`（关组织同步轮询）、各 LLM Provider 的 API Key
（DeepSeek/Qwen/OpenAI/Ollama，见 llm/openai_compatible.py）。

## 7. 当前状态与进行中的工作

- **测试**：482 passed, 1 skipped（skip = 缺 cryptography 时的降级验证）
- **PR #1**：`trae/agent-ig6j8A` → `main`（90+ 提交）待合并，含第十一批优化：
  多 IM 渠道抽象 / 组织同步 / Web Push / 时间线内联回复
- **远程仓库**：https://github.com/laoouzi/guanwang
- **已知的骨架桩（有意为之，非遗漏）**：
  - `im/wecom.py` / `im/dingtalk.py` —— 企微/钉钉真实 API 未接（TODO 已写明步骤）
  - `im/orgsync.py::FeishuOrgSync` —— 飞书通讯录真实拉取未接（contact API）
- **测试脆弱点注意**：断言员工/成员列表时**按 id 索引**，不要依赖列表顺序
  （底层是目录 glob，顺序不保证）。

## 8. 给 AI 的行为约定

1. **先读再改**：动任何文件前先 Read；本文件的目录索引就是地图。
2. **不引入硬依赖**：核心保持标准库；`cryptography` 已是可选上限。
3. **前端单文件**：dashboard.html 不拆分、不引框架、不加构建步骤。
4. **推送/通知类逻辑**：失败必须静默降级（消息总线才是事实源），绝不让
   推送异常冒泡打断业务。
5. **人机同构**：任何员工相关的新能力默认考虑 ai 与 human 两种 kind。
6. **改完跑测试**：`pytest tests/ -q` 全绿才算完；涉及前端的改动跑
   `scripts/mobile_view_check.py`。
7. **提交规范**：中文提交消息，`feat:/fix:/test:/docs:` 前缀，正文讲「为什么」。
