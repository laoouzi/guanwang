# laoban — 像经营公司一样管理 AI 员工

laoban 是一个开源、自托管的多 Agent 编排框架：让你像经营真实公司一样经营一支 AI 员工团队——招聘、入职、派单、评审、驳回、考核，组织随业务自动生长。**AI 员工与人类员工同在一个部门树里协作**（`kind=ai / human`），每个人类员工每天有自己的任务清单。

## 快速上手

```bash
pip install -e .
laoban demo              # 演示模式（无需 API Key，MockLLM 跑通全流程含人机协作）
laoban org init-config   # 生成组织配置模板 .laoban/org.json（部门/岗位/权限）
laoban org show          # 查看组织配置（org.json 优先，否则内置默认模板）
laoban org load          # 按配置批量入职（--founders-only / --team-only 可选）
laoban init              # 初始化你的公司目录（.laoban/）
laoban hire --name 阿码 --title 开发工程师 --department dev_dept
laoban hire --name 陈工 --kind human --title 数据核查员 --department dev_dept
laoban task submit --title "写一个数据清洗函数"
laoban task assign --id T-xxxx --to dev   # 派发：任务入 dev 工位队列
laoban queue --who dev                    # 查看员工工位任务队列
laoban msg send --from pm --to dev --content "请优先处理"
laoban msg inbox --who dev                # 员工收件箱（最新在前）
laoban employee suspend --id dev          # 停职（派单守卫自动拦截）
laoban employee activate --id dev         # 上岗（恢复接单）
laoban employee terminate --id dev        # 解雇（不可逆）
laoban todo add --assignee emp-陈工 --title "配合 AI 核查数据" --due 2026-08-30
laoban today --who emp-陈工          # 人类员工当日任务清单
laoban todo add --assignee emp-小李 --title "复核异常值" --source self --from emp-陈工
laoban todo results --who emp-陈工   # 人→人闭环：查看发起任务回传的结果
laoban auth passwd --who emp-chen        # 给员工设口令（设过即启用看板登录）
laoban dashboard          # Web 看板（127.0.0.1:7891；配好 LLM Key 后派单即自动执行）
```

## 核心概念

| 概念 | 说明 |
|---|---|
| 任务驾驶舱 | 看板网页直接完成：提交任务 → 派单 → 验收评分（1-5，回写员工记忆+记账）→ 审批队列处理；操作按 RBAC 角色收权（staff 仅提交，manager 本部门，admin 全量） |
| 绩效账本 | 完成数 / 成本 / 驳回率 / **人类介入率**（衡量 AI 自主程度）；验收/审批实时记账，落盘 `ledger.json` 重启不丢 |
| 员工 Employee | 人机统一身份：AI（`kind=ai`，档案化运行）与人类（`kind=human`，入部门树）共用部门/汇报/绩效体系 |
| 员工生命周期 | 招聘 → 上岗 → 停职（suspended，可恢复）→ 解雇（terminated，**不可逆**）；非 active 员工被派单守卫拦截 |
| 工位任务队列 | `workspace.queue`：`task assign` 派发入队、完成出队，员工视角的任务清单 |
| 点对点消息 | Messenger：员工间消息（`collaboration` 空白名单=组织内默认开放；非空=白名单收紧） |
| AI 自主协作 | AI 员工在 prompt 中看到**组织通讯录**（职责/能力/忙闲/状态），通过 `[TOOL]` 协议调用 `send_message` / `delegate_task` 工具主动找人；权限拒绝以 ❌ 反馈回 LLM 重试 |
| 任务状态机 | pending → triage → planning → review ⇄ 驳回(≤3 轮) → assigned → doing ⇄ waiting_human → reporting → done |
| 权限矩阵 | 协作权限 / 工具权限 / 支出限额 / 自主等级（supervised/semi/full） |
| 审批队列 | 高危操作 + 支出授权 + 编制申请统一审批单，容量/时间联合触发批量处理 |
| 启动模式 | 创业四元老（HR/法务/IT/财务）基于业务构想产出组织设计方案；组织结构由 `org.json` 配置驱动（v0.2） |
| 双轨招聘 | 轨道 A：老板直招；轨道 B：部门负责人编制申请（新增 AI / 复制 AI / 招聘人类）→ 审批 → 入职；role 命中 `org.json` 岗位模板时自动套用模型/权限 |
| 绩效账本 | 完成数 / 成本 / 驳回率 / **人类介入率**（衡量 AI 自主程度） |
| 经验回写 | 人类验收评分（1-5）回写员工记忆，越用越懂你的业务 |
| AI 复盘 | 评语为空或低分（≤2）时自动复盘：LLM 依据任务要求+交付物生成具体教训（失败降级模板），落 `memory.experiences`，下次执行注入 system prompt（教训排前）——真实链路验证：低分→复盘→重试交付质量改善 |
| 奖励积分 | 人类与 AI **统一记分**：验收通过 +10×(评分/5)、驳回 -5，落盘 `ledger.json`；看板三榜（人类/AI 分榜 + ROI 统一榜）横向对比「谁干活好干活多」 |
| 成本与 ROI | AI 按 token 单价、人类按时薪折算任务成本；ROI = 积分/累计成本（每元成本产出多少业绩），跨族群公平对比 |
| 晋升通道 | **积分达标自动申请**：AI 达 30 分即可升自主等级（supervised→semi→full）；人类走年度评估（入职满一年 + 积分达 30 分）升部门负责人；老板审批后生效，驳回需再攒一档积分 |
| 复盘报告 | 按部门聚合：完成数 / 驳回次数 / 教训数 / 自动复盘数 + 成员明细（最新教训、自主等级）；可见范围与绩效面板一致 |
| 人类待办收件箱 | AI 超出能力时派发结构化人类待办（背景+目标+交付物格式+截止），完成后流程自动恢复 |

## 架构

- **制度内核**（`laoban/core/`）：任务状态机、权限矩阵、员工档案、绩效账本、人类收件箱、经验回写
- **执行引擎**（`laoban/runner/`、`laoban/llm/`）：多供应商 LLM 网关（DeepSeek/千问/GPT/Ollama，OpenAI 兼容）、工具循环、安全 Guard、评审员（准奏/封驳）、审批队列、审批日志
- **交互层**（`laoban/cli.py`、`laoban/dashboard/`）：CLI 完整子命令 + Web 看板（标准库 HTTP，零第三方依赖）

## 配置参考

### 组织配置 org.json（v0.2）

部门/岗位/权限全部配置化：`laoban org init-config` 生成 `.laoban/org.json`，
编辑后 `laoban org load` 批量入职。未提供配置时使用内置默认模板
（`laoban/templates/default_org.json`：6 部门 10 岗位，含 HR/法务/IT/财务
四元老创始人——财务专家负责成本核算与 ROI 分析）。

```json
{
  "company": "我的公司",
  "business": "跨境电商工具",
  "departments": [
    {
      "id": "dev_dept", "name": "研发部", "mission": "功能开发与数据交付",
      "roles": [
        {
          "id": "dev", "name": "阿码", "kind": "ai", "title": "开发工程师",
          "founder": false,
          "model": {"provider": "deepseek", "model": "deepseek-chat"},
          "job_description": {"mission": "按任务要求产出代码与数据交付物"},
          "capabilities": {"tools": ["python_exec", "file_write"]},
          "permissions": {"can_assign_human_tasks": true,
                          "spending_limit_per_task": 5.0, "autonomy_level": "semi"}
        },
        {"id": "emp-chen", "name": "陈工", "kind": "human", "title": "数据核查员"}
      ]
    }
  ]
}
```

规则：
- 岗位字段 `model` / `job_description` / `performance_goals` / `capabilities` / `permissions` / `compensation`
  为**合并覆盖**（缺省字段用 `Employee` 默认值兜底）；
- `founder: true` 的角色 = 启动模式创始人（`bootstrap` 只入职这些人）；
- `compensation` 定成本：AI 配 `cost_per_1k_tokens`（token 单价），人类配 `salary_monthly`
  （按时薪 = 月薪/22天/8小时折算），未配置者成本记 0（不虚造数据）；
- 双轨招聘编制申请的 `role` 按**岗位 id 或 title** 命中模板时，新员工自动套用该岗位的
  模型/权限/职责（`kind` 以申请单为准）；
- 配置查找顺序：显式 `--file` → `{root}/org.json` → 内置默认模板。

### AI 自主协作（Runner 工具循环）

`Runner(gateway, store=...)` 注入 store 后，AI 员工的执行循环变为：

1. **看**：system prompt 携带组织通讯录（`render_directory`：`[AI/人类] id 姓名 · 职务 · 部门 · 职责 · 工具 · 在办N · 停职标注`，解雇员工不可见）；
2. **选**：LLM 输出 `[TOOL]` 块发起协作——`send_message`（发消息）或 `delegate_task`（派子任务：人类→待办收件箱且结果回传发起人；AI→新任务 born-assigned 入其工位队列）；
3. **守**：权限守卫复用制度管道（`can_message` 白名单、`can_assign_human_tasks`、在职校验）；拒绝不抛异常，以 `❌` 反馈回 LLM 换人重试（最多 3 轮）；
4. **审**：所有协作动作以 `[协作动作]` 附在产出尾部，可审计。

```
[TOOL] delegate_task
{"assignee": "emp-chen", "title": "核查异常值", "instruction": "核对后回传"}
[/TOOL]
```

### 自动运转引擎（WorkerLoop）

`laoban dashboard` 检测到真实 LLM（任一 `LAOBAN_*` 环境变量）时默认启动后台 `WorkerLoop` 线程：

1. **扫**：每 `--worker-interval` 秒（默认 2.0）轮询所有在职 AI 员工的工位队列；
2. **执行**：发现 `assigned` 任务即自动推进 `doing` → 调 `Runner` 执行 → `reporting`（交付物落 `progress_log`）；
3. **容错**：LLM 调用失败不炸循环，任务转 `blocked` 并写 `block_reason`，下轮不再重复执行（幂等）；
4. **人的位置**：全程零人工干预，你只在看板做两件事——**验收评分**（→ `done`，绩效入账）或处理**审批单**。

```bash
laoban dashboard                          # 默认：自动运转开启
laoban dashboard --no-worker              # 关闭（纯人工推状态，适合调试）
laoban dashboard --worker-interval 5.0    # 自定义扫描间隔
```

注意：演示模式（MockLLM / 无 Key）不启动 worker，需手动 `laoban task status` 推进——自动运转只在真实 LLM 下默认开启。

### AI 复盘机制（验收 → 教训 → 下次生效）

验收是员工成长的入口：`review_and_learn`（`laoban/core/retro.py`）在验收时自动回写经验——

1. **触发**：老板留了评语且 score≥3 → learned = 评语（现状）；评语为空或低分（≤2）→ 自动复盘；
2. **生成**：有 LLM 网关且承接人是 AI → 把任务要求 + 交付物 + 评分喂给该员工自己的模型，产出 ≤60 字具体教训；LLM 失败/无网关 → 模板教训降级，**复盘永不缺席**；
3. **生效**：教训落 `memory.experiences`（`auto` 标记可审计），下次执行经 `render_experience` 注入 system prompt——**低分教训排最前**，success 经验跟后，各取最近 5 条防 token 膨胀；
4. **响应可见**：验收接口返回 `review` 字段，看板验收后直接展示「复盘（AI 自动复盘/采纳验收评语）：教训内容」。

```
# 真实链路验证（Kimi）：
# 任务1 回文函数 → 交付物被历史对话污染 → 低分验收（无评语）
#   → AI 复盘自动诊断：「交付物混入了无关内容，下次交付前逐行审查」
# 任务2 同类重试 → 交付物干净（教训注入生效）
```

### 奖励积分与成本产出比（人类/AI 统一记分）

记分规则（集中定义在 `laoban/core/points.py`，改规则只动一处）：

```
验收通过   +10 × (score/5)      # 满分 10 分、4 分 8 分、3 分 6 分
验收驳回   -5                   # score ≤ 2 视为驳回（与复盘阈值一致）
任务成本   AI: token用量 × cost_per_1k_tokens 单价（网关自动统计 usage）
           人类: 任务耗时 × 时薪（salary_monthly / 22天 / 8小时）
ROI        = 积分 / 累计成本     # 每元成本产出多少业绩
```

榜单设计（避免不公平对比）：AI 吞任务速度天然碾压人类，绝对分榜会失真——
所以看板 `GET /api/points` 出**三张榜**：

| 榜 | 口径 | 用途 |
|---|---|---|
| 人类员工榜 | 积分降序 | 谁干活好干活多（部门/积分/完成数/驳回/累计成本） |
| AI 员工榜 | 积分降序 | 同口径管 AI 团队 |
| ROI 统一榜 | 积分/成本 降序 | 跨族群公平对比（AI 成本低是真实优势；未配成本单价者不入榜） |

- 可见范围同绩效面板：admin 全量 / manager 本部门 / staff 仅本人；
- 验收响应带 `points`（当前累计分）；成本在任务执行落 `progress_log`（token 用量 + 元），
  验收时入账绩效账本；
- 初创组织自带财务专家（CFO 钱掌柜）：统计各部门与员工成本、核算积分/成本产出比、
  出具成本报告与预算建议。

### 晋升通道（积分驱动 · 双轴晋升）

**积分达标自动申请**，老板审批后生效（放权必须人工把关）：

- **AI 员工 → 自主等级轴**：奖励积分达 30（≈3 次满分验收）即自动申请
  supervised → semi → full；一次升一级，full 封顶；
- **人类员工 → 管理权限轴**：**年度评估**（每年一次）——入职满一年
  （`hired_at` 锚点，晋升后以 `last_promoted_at` 重置）且积分达 30，
  自动申请 staff → manager，晋升即放权（可派单/验收/申请编制，RBAC 即时生效）；
- 防重复：已有 pending 申请不重复提；已授予的目标不再申请；
- 驳回冷却：被驳回后需再攒满一档晋升积分（申请时积分 + 30）才可重新申请；
- 验收响应带 `promotion` 字段，看板提示「📈 晋升申请已自动提交」。

```
验收攒分 → 30 分达标
  AI：   自动申请 semi/full（自主等级）      → 老板批准 → 风险放行范围扩大
  人类： 年度评估（满一年）→ 自动申请 manager → 老板批准 → RBAC 权限即时生效
```

### 复盘报告（GET /api/report）

按部门聚合绩效与教训沉淀（看板「复盘报告」卡片直接渲染）：

```json
[{"department": "dev_dept", "completion_count": 3, "rejections": 1,
  "lessons": 1, "auto_reviews": 1,
  "members": [{"id": "dev", "name": "阿码", "lessons": 1, "wins": 2,
               "autonomy_level": "semi", "latest_lesson": "先写单测再交付"}]}]
```

可见范围：admin 全公司；manager 本部门；staff 仅本人。

### 编制申请看板化（双轨招聘 · 轨道 B 闭环）

部门负责人在驾驶舱直接扩编，老板一键决策：

```
POST /api/headcount/submit   manager/admin 提交（hire_type: new_ai / clone_ai / hire_human）
GET  /api/headcount          admin 全量；manager/staff 仅自己提交的
POST /api/headcount/decide   仅 admin：通过 → HR 自动入职（返回 hired_emp_id）；驳回 → 记理由
```

- 通过后花名册即时 +1（role 命中 `org.json` 模板自动套用；clone_ai 生成源员工分身）；
- 重复决策 409 拦截；员工列表随角色收权（staff 看不到表单与决策按钮）。

### 员工档案字段（`Employee` dataclass）

| 字段 | 类型 | 说明 | 典型值 |
|---|---|---|---|
| `id` / `name` | str | 员工标识与显示名 | `emp-chen`, `陈工` |
| `kind` | `ai` / `human` | 身份类别；人类员工同样入部门树 | `ai` |
| `title` / `department` / `reports_to` | str | 岗位、部门、汇报上级 | `开发工程师`, `dev_dept` |
| `source` | `founder` / `template` / `hired` / `cloned` | 入职来源 | `hired` |
| `status` | `active` / `suspended` / `terminated` | 在职状态 | `active` |
| `hired_at` | str | 入职时间（ISO，年度评估的年限锚点） | `2026-01-01T00:00:00+00:00` |
| `compensation` | dict | 成本口径：AI `cost_per_1k_tokens`；人类 `salary_monthly`（时薪折算） | `{"cost_per_1k_tokens": 0.012}` |
| `job_description` | dict | 核心职能：`mission` / `duties[]` / `workflow_rules[]` / `escalation` | - |
| `performance_goals` | dict | 绩效目标：`max_concurrent` / `budget_daily_cost` / `quality_bar` | - |
| `capabilities` | dict | 工作能力：`tools[]` / `skills[]` / `model_fit[]` | `{"tools": ["file_rw"]}` |
| `model_config` | dict | LLM 配置：`provider` / `model` / `temperature` | `{"provider":"deepseek","model":"deepseek-chat"}` |
| `permissions` | dict | 权限矩阵：`collaboration[]` / `can_assign_human_tasks` / `spending_limit_per_task` / `autonomy_level` | - |
| `permissions.autonomy_level` | `supervised` / `semi` / `full` | 自主等级：supervised 全审批 / semi 放行 low / full 放行 low+medium | `supervised` |
| `memory` | dict | 轻量记忆：`experiences[]` / `notes[]`（由验收评分回写） | - |
| `workspace` | dict | 工位：`dir` / `queue[]` / `context{}` | - |

### LLM 网关（LLMGateway）

```bash
# 演示模式（默认，无需 Key）
laoban demo
laoban acceptance run

# 真实模式：设置任一环境变量即可自动发现
export LAOBAN_DEEPSEEK_API_KEY="sk-..."        # DeepSeek
export LAOBAN_DASHSCOPE_API_KEY="sk-..."       # 通义千问
export LAOBAN_OPENAI_API_KEY="sk-..."          # OpenAI
export LAOBAN_MOONSHOT_API_KEY="sk-..."        # Kimi（kimi-k2.6）
export LAOBAN_OLLAMA_BASE_URL="http://127.0.0.1:11434/v1"   # Ollama（本地）

laoban acceptance run                          # 自动切换真实 LLM 跑 D2 验收
laoban acceptance run --provider deepseek      # 多个 Key 时指定用哪个
```

```python
from laoban.llm.gateway import LLMGateway
from laoban.llm.openai_compatible import register_from_env, OpenAICompatibleProvider

gw = LLMGateway()
register_from_env(gw)                          # 环境变量自动注册
# 或手动注册任意 OpenAI 兼容服务（自建网关/vLLM/LM Studio 等）
gw.register_provider("my-llm", OpenAICompatibleProvider(
    base_url="http://10.0.0.5:8000/v1", api_key="token", model="Qwen2.5-32B"))
```

传输层用标准库 `urllib` 实现（零第三方依赖），协议为 OpenAI 兼容 `/chat/completions`；
路由原则：`chat_for_employee(model_config)` → 用 `model_config["provider"]` 查找已注册的实现，不依赖员工 ID。

### 权限矩阵 · 分级放行（`should_approve`）

| 风险 / 自主 | supervised | semi | full |
|---|---|---|---|
| `high`（命令、越界写文件）| ✅ 审批 | ✅ 审批 | ✅ **审批（永远）** |
| `medium`（非白名单域名）| ✅ 审批 | ✅ 审批 | 放行 |
| `low`（workspaces 内读写）| ✅ 审批 | 放行 | 放行 |

高危操作经 `request_and_maybe_block()` 统一入口，100% 落盘到 `approvals/`（D6 硬保障）。

### 消息权限（`can_message`）

点对点通信使用宽松规则（与工具协作的严格白名单 `can_collaborate` 区分）：
`permissions.collaboration` **为空 = 组织内默认可联系任何人**；非空 = 白名单模式（只能联系白名单内员工）。
非在职（suspended/terminated）员工不可发送消息。

### 员工鉴权（看板登录）

看板默认免鉴权（本地单人使用）。给任一员工设过口令后，看板自动启用登录：

```bash
laoban auth passwd --who emp-chen          # 交互输入口令（或 --password 指定）
laoban auth list                           # 查看已设口令的员工
laoban auth remove --who emp-chen          # 清除口令（全部清除后回到免鉴权模式）
```

- 口令以 **PBKDF2-HMAC-SHA256（12 万轮 + 随机盐）** 落盘 `.laoban/auth.json`，不存明文；
- 登录后发 HttpOnly 会话 Cookie；聊天强制以本人身份发送（冒充他人 → 403）；
- 登录后看板锁定身份输入框，AI 同事下拉可选（含在职状态），支持载入历史消息。

### 权限差异化视图（RBAC-lite）

登录后不同角色看到的内容不同（未设口令 = 免鉴权模式，所有人看全量，向后兼容）：

| 角色 | 判定 | 花名册/组织架构 | 任务 | 消息/回传结果 | 队列/当日待办 |
|---|---|---|---|---|---|
| **admin** | `permissions.role=admin` | 全公司全字段 | 全量 | 本人或任意人 | 本人或任意人 |
| **manager** | `permissions.role=manager`，或有人 `reports_to` 指向他（自动升级） | 全公司；本部门全字段，跨部门脱敏 | 本部门相关 | 仅本人（下属私信不可看） | 本人 + 本部门成员 |
| **staff**（默认） | 其余员工 | 仅本部门 + 自己；他人脱敏 | 本部门相关 | 仅本人 | 仅本人 |

- 脱敏 = 去掉 `permissions` / `memory` / `model_config` 敏感字段（自己看自己始终全字段）；
- 任务「相关」= flow_log 中出现过本部门成员；无任何 actor 的新任务仅 admin 可见；
- 角色可在 `org.json` 岗位模板里直接配（如上例 `permissions.role`）；
- 前端登录后显示角色徽章与可见范围，个人查询框按角色锁定（staff 全锁、manager 锁消息）。

### 人↔AI 聊天与 IM 渠道接入

消息总线是唯一事实源，渠道只是入口/出口：

```bash
# 1. 看板聊天框（配好 LLM Key 后 laoban dashboard，页面直接与 AI 员工对话）

# 2. 飞书机器人接入
export LAOBAN_FEISHU_APP_ID=cli_xxx
export LAOBAN_FEISHU_APP_SECRET=xxx
# 可选：LAOBAN_FEISHU_VERIFICATION_TOKEN（事件 token 校验）
#       LAOBAN_FEISHU_ENCRYPT_KEY（事件加密，需 pip install pycryptodome 或 cryptography）
#       LAOBAN_FEISHU_BOT_OPEN_ID（群聊 @识别；不配则任何 @ 都视为 @机器人）
#       LAOBAN_IM_DEFAULT_TO（默认收件人）
laoban im bind --platform feishu --im-user ou_xxx --employee emp-chen   # IM 账号 ↔ 员工
laoban dashboard   # 事件回调 URL 填 http://<本机>:7891/api/im/webhook/feishu（需公网/内网穿透）
```

员工在飞书里给机器人发 `dev: 数据放哪了？`（格式「同事id: 内容」）→ 消息落总线 →
AI 回信推回飞书；收件人是人类同事时只投递，若对方也绑定了则同步推送其 IM（人↔人经总线中转）。
事件 ACK 后台线程回信（飞书 3 秒 ACK 要求），event_id 去重防重试风暴。

- **私聊（DM）**：文本消息直接处理，回信 DM 提问者；
- **群聊**：只有 @机器人 的消息才处理（不吵群），@提及 token 剥离后照常解析
  「同事id: 内容」，回信 / 错误提示 / 投递回执推回群里（chat_id），
  人→人中转仍走对方 DM；
- **加密事件**：配置 `LAOBAN_FEISHU_ENCRYPT_KEY` 后支持
  AES-256-CBC（key=SHA256(encrypt_key)）密文体，未配 key 收到密文返回 400。

### 验收套件（D2）

```bash
laoban acceptance run --root /tmp/laoban-acc
# 分类：
#   ACCEPT-DEV-001  开发类（odd_sum.py + 单元测试 5 条 → unittest 全绿）
#   ACCEPT-DOC-001  文档类（doc.md 四章节齐全且每章 ≥30 字）
#   ACCEPT-DATA-001 数据类（data.csv 10 条 × summary.json 五指标误差 ≤0.5）
```

验收 runner 除自动判定外，还会调用评审员（`Reviewer`）做 LLM 检查单复核，双层保障。

### 目录结构

```
.laoban/                      # laoban init 生成
├── org.json                  # 组织配置（laoban org init-config 生成，可选）
├── tasks/<id>.json           # 任务档案（含 flow_log + progress_log）
├── employees/<id>.json       # 员工档案
├── messages/<id>.json        # 点对点消息（MSG-*）
├── im_bindings.json          # IM 账号 ↔ 员工 id 绑定表（laoban im bind）
├── auth.json                 # 员工口令库（PBKDF2 盐+哈希，laoban auth passwd）
├── ledger.json               # 绩效账本（看板验收/审批自动记账）
├── approvals/                # 审批单档案（每单一 JSON，看板可处理）
├── human_tasks/<id>.json     # 人类待办（AI 派发的配合任务 / 自建 / 老板指派）
├── headcount_requests/       # 编制申请（双轨招聘）
└── workspaces/<emp_id>/      # 员工工位产出物
```

## 测试

```bash
python -m pytest tests/ -q                 # 全量回归（302 用例）
python scripts/dashboard_worker_check.py   # 自动运转 e2e（Playwright + 假 LLM，无需 Key）
```

## 安全声明

- 本地运行默认无鉴权，看板仅绑定 127.0.0.1，请勿暴露公网；
- 高危操作默认需人工审批，高风险操作不可自动放行；
- 法务专家提示为常识级参考，不构成法律意见；AI 产出由部署者承担使用责任。

## License

MIT
