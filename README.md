# laoban — 像经营公司一样管理 AI 员工

laoban 是一个开源、自托管的多 Agent 编排框架：让你像经营真实公司一样经营一支 AI 员工团队——招聘、入职、派单、评审、驳回、考核，组织随业务自动生长。**AI 员工与人类员工同在一个部门树里协作**（`kind=ai / human`），每个人类员工每天有自己的任务清单。

## 快速上手

```bash
pip install -e .
laoban demo              # 演示模式（无需 API Key，MockLLM 跑通全流程含人机协作）
laoban init              # 初始化你的公司目录（.laoban/）
laoban hire --name 阿码 --title 开发工程师 --department dev_dept
laoban hire --name 陈工 --kind human --title 数据核查员 --department dev_dept
laoban task submit --title "写一个数据清洗函数"
laoban todo add --assignee emp-陈工 --title "配合 AI 核查数据" --due 2026-08-30
laoban today --who emp-陈工          # 人类员工当日任务清单
laoban dashboard          # Web 看板（127.0.0.1:7891）
```

## 核心概念

| 概念 | 说明 |
|---|---|
| 员工 Employee | 人机统一身份：AI（`kind=ai`，档案化运行）与人类（`kind=human`，入部门树）共用部门/汇报/绩效体系 |
| 任务状态机 | pending → triage → planning → review ⇄ 驳回(≤3 轮) → assigned → doing ⇄ waiting_human → reporting → done |
| 权限矩阵 | 协作权限 / 工具权限 / 支出限额 / 自主等级（supervised/semi/full） |
| 审批队列 | 高危操作 + 支出授权 + 编制申请统一审批单，容量/时间联合触发批量处理 |
| 启动模式 | 创业三元老（HR/法务/IT）基于业务构想产出组织设计方案 |
| 双轨招聘 | 轨道 A：老板直招；轨道 B：部门负责人编制申请（新增 AI / 复制 AI / 招聘人类）→ 审批 → 入职 |
| 绩效账本 | 完成数 / 成本 / 驳回率 / **人类介入率**（衡量 AI 自主程度） |
| 经验回写 | 人类验收评分（1-5）回写员工记忆，越用越懂你的业务 |
| 人类待办收件箱 | AI 超出能力时派发结构化人类待办（背景+目标+交付物格式+截止），完成后流程自动恢复 |

## 架构

- **制度内核**（`laoban/core/`）：任务状态机、权限矩阵、员工档案、绩效账本、人类收件箱、经验回写
- **执行引擎**（`laoban/runner/`、`laoban/llm/`）：多供应商 LLM 网关（DeepSeek/千问/GPT/Ollama，OpenAI 兼容）、工具循环、安全 Guard、评审员（准奏/封驳）、审批队列
- **交互层**（`laoban/cli.py`、`laoban/dashboard/`）：CLI 完整子命令 + Web 看板（标准库 HTTP，零第三方依赖）

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
