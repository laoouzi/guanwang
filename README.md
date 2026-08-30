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
laoban todo add --assignee emp-陈工 --title "配合 AI 核查数据" --due 2026-08-30
laoban today --who emp-陈工          # 人类员工当日任务清单
laoban todo add --assignee emp-小李 --title "复核异常值" --source self --from emp-陈工
laoban todo results --who emp-陈工   # 人→人闭环：查看发起任务回传的结果
laoban dashboard          # Web 看板（127.0.0.1:7891）
```

## 核心概念

| 概念 | 说明 |
|---|---|
| 员工 Employee | 人机统一身份：AI（`kind=ai`，档案化运行）与人类（`kind=human`，入部门树）共用部门/汇报/绩效体系 |
| 任务状态机 | pending → triage → planning → review ⇄ 驳回(≤3 轮) → assigned → doing ⇄ waiting_human → reporting → done |
| 权限矩阵 | 协作权限 / 工具权限 / 支出限额 / 自主等级（supervised/semi/full） |
| 审批队列 | 高危操作 + 支出授权 + 编制申请统一审批单，容量/时间联合触发批量处理 |
| 启动模式 | 创业三元老（HR/法务/IT）基于业务构想产出组织设计方案；组织结构由 `org.json` 配置驱动（v0.2） |
| 双轨招聘 | 轨道 A：老板直招；轨道 B：部门负责人编制申请（新增 AI / 复制 AI / 招聘人类）→ 审批 → 入职；role 命中 `org.json` 岗位模板时自动套用模型/权限 |
| 绩效账本 | 完成数 / 成本 / 驳回率 / **人类介入率**（衡量 AI 自主程度） |
| 经验回写 | 人类验收评分（1-5）回写员工记忆，越用越懂你的业务 |
| 人类待办收件箱 | AI 超出能力时派发结构化人类待办（背景+目标+交付物格式+截止），完成后流程自动恢复 |

## 架构

- **制度内核**（`laoban/core/`）：任务状态机、权限矩阵、员工档案、绩效账本、人类收件箱、经验回写
- **执行引擎**（`laoban/runner/`、`laoban/llm/`）：多供应商 LLM 网关（DeepSeek/千问/GPT/Ollama，OpenAI 兼容）、工具循环、安全 Guard、评审员（准奏/封驳）、审批队列、审批日志
- **交互层**（`laoban/cli.py`、`laoban/dashboard/`）：CLI 完整子命令 + Web 看板（标准库 HTTP，零第三方依赖）

## 配置参考

### 组织配置 org.json（v0.2）

部门/岗位/权限全部配置化：`laoban org init-config` 生成 `.laoban/org.json`，
编辑后 `laoban org load` 批量入职。未提供配置时使用内置默认模板
（`laoban/templates/default_org.json`：5 部门 9 岗位，含三元老创始人）。

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
- 岗位字段 `model` / `job_description` / `performance_goals` / `capabilities` / `permissions`
  为**合并覆盖**（缺省字段用 `Employee` 默认值兜底）；
- `founder: true` 的角色 = 启动模式创始人（`bootstrap` 只入职这些人）；
- 双轨招聘编制申请的 `role` 按**岗位 id 或 title** 命中模板时，新员工自动套用该岗位的
  模型/权限/职责（`kind` 以申请单为准）；
- 配置查找顺序：显式 `--file` → `{root}/org.json` → 内置默认模板。

### 员工档案字段（`Employee` dataclass）

| 字段 | 类型 | 说明 | 典型值 |
|---|---|---|---|
| `id` / `name` | str | 员工标识与显示名 | `emp-chen`, `陈工` |
| `kind` | `ai` / `human` | 身份类别；人类员工同样入部门树 | `ai` |
| `title` / `department` / `reports_to` | str | 岗位、部门、汇报上级 | `开发工程师`, `dev_dept` |
| `source` | `founder` / `template` / `hired` / `cloned` | 入职来源 | `hired` |
| `status` | `active` / `suspended` / `terminated` | 在职状态 | `active` |
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
├── human_tasks/<id>.json     # 人类待办（AI 派发的配合任务 / 自建 / 老板指派）
├── headcount_requests/       # 编制申请（双轨招聘）
├── approvals/<id>.json       # 审批日志（高危必记）
└── workspaces/<emp_id>/      # 员工工位产出物
```

## 测试

```bash
python -m unittest discover -v
```

## 安全声明

- 本地运行默认无鉴权，看板仅绑定 127.0.0.1，请勿暴露公网；
- 高危操作默认需人工审批，高风险操作不可自动放行；
- 法务专家提示为常识级参考，不构成法律意见；AI 产出由部署者承担使用责任。

## License

MIT
