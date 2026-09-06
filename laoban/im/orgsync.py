"""组织/人员同步：IM 通讯录 → 平台 Employee + Bindings（增量融合）。

数据源抽象 OrgSyncSource（飞书/企微/钉钉各自实现「拉取」），sync_org 负责
把标准化通讯录融合进 store：

- 人类员工按「已有 IM 绑定」或「姓名」匹配现有员工，更新部门/汇报链；
- 匹配不到的成员默认不新建（sync_create=True 才拉入平台，避免把通讯录里
  无关的人全拽进来）；
- AI 员工（IM 通讯录里没有）一概不动——同步只覆盖人类员工；
- 成员账号自动写入 Bindings（platform → im_user），催办/摘要推送即自动触达。

「实时」程度：接通讯录变更事件 webhook 可到秒级；本模块先做定时轮询
（OrgSyncSweeper）+ 看板手动触发（POST /api/org/sync），兜底够用。

标准化协议：
  department = {"id", "name", "parent_id"}
  member     = {"id"(IM 账号), "name", "department_ids"[..], "manager_id"(上级 IM 账号)}
"""
from __future__ import annotations

import threading

from ..core.store import JsonStore
from .binding import Bindings


class OrgSyncSource:
    """组织同步数据源：一个 IM 渠道的通讯录拉取，返回标准化部门/成员。"""

    platform = ""

    def fetch_departments(self) -> list[dict]:
        raise NotImplementedError

    def fetch_members(self) -> list[dict]:
        raise NotImplementedError


class FeishuOrgSync(OrgSyncSource):
    """飞书通讯录 → 标准化（骨架桩：TODO 接入 contact API）。

    接入要点（零依赖 urllib，仿 feishu.py 的 FeishuClient）：
      - 部门：GET {base}/open-apis/contact/v3/departments
        （带 user_access_token 或 tenant token + scope contact:department.base:readonly）
      - 成员：GET {base}/open-apis/contact/v3/users/find_by_department
        （scope contact:user.base:readonly）
      - 映射：部门 id/name/parent → department；用户 open_id/name/department_ids/
        manager → member。
    """

    platform = "feishu"

    def __init__(self, client=None):
        self.client = client   # FeishuClient（提供 tenant token 与请求通道）

    def fetch_departments(self) -> list[dict]:
        return []   # TODO(feishu-sync): 拉取部门树

    def fetch_members(self) -> list[dict]:
        return []   # TODO(feishu-sync): 拉取成员


def sync_org(store: JsonStore, source: OrgSyncSource, bindings: Bindings,
             sync_create: bool = False) -> dict:
    """把 source 通讯录融合进 store，返回统计 {created, updated, bound}。

    匹配优先级：已有 IM 绑定 → 姓名（仅人类员工）；都匹配不到且 sync_create
    才新建。汇报链（manager_id 为 IM 账号）先解析成员工 id 再回填 reports_to。
    """
    depts = source.fetch_departments()
    members = source.fetch_members()
    dept_name = {d.get("id"): d.get("name", "") for d in depts if d.get("id")}
    platform = source.platform

    # 第一遍：im_id → 员工 id（已有绑定 + 姓名匹配）
    im_to_emp: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for e in store.list_employees():
        if e.kind == "human":
            by_name.setdefault(e.name, e.id)
    for m in members:
        im_id = m.get("id")
        if not im_id:
            continue
        emp_id = bindings.lookup(platform, im_id)
        if not emp_id:
            emp_id = by_name.get(m.get("name", ""), "")
        if emp_id:
            im_to_emp[im_id] = emp_id

    created = updated = bound = 0
    for m in members:
        im_id = m.get("id")
        if not im_id:
            continue
        emp_id = im_to_emp.get(im_id)
        if not emp_id:
            if not sync_create or not m.get("name"):
                continue
            # 新建人类员工：以「平台 + IM 账号」作内部 id，避免与 AI 员工撞名
            from ..core.employee import Employee
            e = Employee(id=f"{platform}-{im_id}", name=m["name"], kind="human")
            store.save_employee(e)
            emp_id = e.id
            created += 1
        else:
            e = store.load_employee(emp_id)
            if e is None or e.kind != "human":
                continue   # 匹配到 AI 员工（同名人）→ 跳过，不动 AI
            updated += 1

        # 部门：取第一个有名字的部门
        dept_ids = m.get("department_ids") or []
        dept = next((dept_name.get(d, "") for d in dept_ids
                     if dept_name.get(d)), "")
        if dept:
            e.department = dept
        # 汇报链：manager_id 是 IM 账号 → 员工 id。
        # 先查本轮成员映射（上级同在本批成员里），再回退到已有绑定（上级
        # 之前已绑定/不在本批成员里）。
        mgr = m.get("manager_id", "")
        mgr_emp = im_to_emp.get(mgr, "") or bindings.lookup(platform, mgr) or ""
        if mgr_emp:
            e.reports_to = mgr_emp
        store.save_employee(e)
        bindings.bind(platform, im_id, emp_id)
        bound += 1

    return {"created": created, "updated": updated, "bound": bound,
            "departments": len(depts), "members": len(members)}


class OrgSyncSweeper(threading.Thread):
    """后台定时轮询：对每个已注册的 OrgSyncSource 拉取并融合。

    环境变量：LAOBAN_ORG_SYNC=0 关闭；LAOBAN_ORG_SYNC_SEC 间隔（默认 3600）。
    """

    def __init__(self, store: JsonStore, sources: list, bindings: Bindings,
                 interval_sec: float = 3600.0, sync_create: bool = False):
        super().__init__(daemon=True, name="laoban-org-sync")
        self.store = store
        self.sources = sources
        self.bindings = bindings
        self.interval = max(10.0, float(interval_sec))
        self.sync_create = sync_create
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(self.interval):
            self.sweep()

    def sweep(self) -> list[dict]:
        results = []
        for src in self.sources:
            try:
                r = sync_org(self.store, src, self.bindings,
                             sync_create=self.sync_create)
                r["platform"] = src.platform
                results.append(r)
                print(f"[org-sync] {src.platform}：{r}")
            except Exception as e:
                print(f"[org-sync] {src.platform} 同步失败：{e!r}")
        return results

    def stop(self):
        self._stop.set()
