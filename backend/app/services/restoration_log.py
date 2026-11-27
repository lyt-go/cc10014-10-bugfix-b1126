"""修缮日志领域服务：每日记录施工内容和材料用量，与工序编排、质量检查联动。

封存边界：
- 档案入库后，已审核日志按同一封存凭据纳入档案，未到审核节点的日志保留补写空间；
- 工匠重新打开日志可通过 seal_boundary 知道本条是否已纳入档案、下次是否还需补交；
- 审核通过（补报）与档案入库按工序级互斥锁串行，边界可查、结果不交叉。
"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class RestorationLogService(BaseModuleService):
    MODULE = "restoration_log"
    ENTITY = "施工日志"
    OPERATION_TERM = "提交日志"
    LIST_FIELDS = ["施工日期", "对应工序", "施工内容", "用工数量", "材料用量", "存在问题", "记录人", "备注说明"]
    REQUIRED_FIELDS = ["施工日期", "对应工序", "施工内容"]
    STATUS_ORDER = ["待填写", "已填写", "已审核", "已归档"]
    ACTION_RULES = {"提交日志": "已填写", "审核通过": "已审核", "归档日志": "已归档"}
    NEGATIVE_ACTIONS = []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "提交日志":
            process = entry.get("对应工序", "")
            # 关联工序必须已发布或施工中
            ws_rows = store.query_by_field("work_sequence", "工序名称", process)
            if ws_rows and all(r.get("status") not in ("已发布", "施工中", "已完成") for r in ws_rows):
                return False, f"关联工序「{process}」状态不允许提交日志"
        if action == "审核通过":
            process = entry.get("对应工序", "")
            # 如果日志提到材料用量，对应材料核验单需已签认
            material_used = entry.get("材料用量", "")
            if material_used and material_used not in ("", "无", "0"):
                verified = [
                    r for r in store.rows("material_verify")
                    if r.get("材料名称", "") in material_used and r.get("status") == "已签认"
                ]
                if material_used and not verified:
                    return False, f"日志中材料「{material_used}」未经过核验签认"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> list[dict]:
        effects = []
        if action == "审核通过":
            process = entry.get("对应工序", "")
            # 日志审核通过后联动排期执行中
            for sched in store.rows("construction_schedule"):
                if sched.get("计划工序", "") == process and sched.get("status") == "已发布":
                    sched["status"] = "执行中"
                    effects.append(dict(sched))
            # 补报：本工序已有封存凭据时，审核通过即按原凭据补纳入档案（不另开封存边界）
            latest_seal = store.latest_seal_for_process(process)
            if latest_seal is not None:
                entry["封存凭据"] = latest_seal["seal_no"]
                entry["档案ID"] = latest_seal["archive_id"]
                entry["封存时间"] = time.time()
                entry["补交状态"] = "后补纳入"
                entry["待补交凭据"] = ""
                latest_seal.setdefault("supplement_log_ids", []).append(entry_id)
                store.register_seal(latest_seal)
                effects.append(dict(entry))
        if action == "归档日志":
            # 检查是否可以联动工序完成
            process = entry.get("对应工序", "")
            logs_for_process = [
                r for r in store.rows(self.MODULE)
                if r.get("对应工序", "") == process
            ]
            if all(l.get("status") == "已归档" for l in logs_for_process):
                for ws_row in store.rows("work_sequence"):
                    if ws_row.get("工序名称", "") == process and ws_row.get("status") == "施工中":
                        ws_row["status"] = "已完成"
                        ws_row["pending"] = False
                        effects.append(dict(ws_row))
        return effects

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        # 与档案入库共用工序级锁：提交/审核/归档全流程与封存串行，边界可查、结果不交叉
        entry = store.find(self.MODULE, entry_id)
        process_key = ""
        if entry is not None and action in ("提交日志", "审核通过", "归档日志"):
            process_key = f"seal-process:{entry.get('对应工序', '')}"
        if process_key and not store.acquire_resource(process_key):
            return None, "该工序正在封存档案，请稍后重试"
        try:
            return super().run_action(entry_id, action)
        finally:
            if process_key:
                store.release_resource(process_key)

    # ── 封存边界查询：工匠重新打开日志时判断是否已纳入、是否还要补交 ──
    def seal_boundary(self, entry_id: int) -> dict[str, Any]:
        log = store.find(self.MODULE, entry_id)
        if log is None:
            return {"ok": False, "message": f"施工日志 {entry_id} 不存在"}

        process = str(log.get("对应工序", "") or "")
        seals = store.seals_for_process(process)
        seal_no = str(log.get("封存凭据", "") or "")
        included = bool(seal_no)

        if included:
            archive = next(
                (row for row in store.rows("archive_mgmt")
                 if str(row.get("封存凭据", "") or "") == seal_no),
                None,
            )
            return {
                "ok": True,
                "log_id": entry_id,
                "process": process,
                "included": True,
                "seal_no": seal_no,
                "archive_id": log.get("档案ID"),
                "archive_name": archive.get("档案名称") if archive else "",
                "补交状态": log.get("补交状态", "已纳入"),
                "need_resubmit": False,
                "message": f"本条记录已纳入档案，封存凭据 {seal_no}，无需重复补交",
                "process_seals": seals,
            }

        # 未纳入：判断本工序是否已存在封存边界，以及本条下一次是否还要补交
        pending_seal = str(log.get("待补交凭据", "") or "")
        if not pending_seal and seals:
            pending_seal = seals[-1]["seal_no"]
        if seals:
            need = log.get("status") in ("待填写", "已填写")
            message = (
                f"本条记录尚未纳入档案，下一次审核通过后将补交到封存凭据 {pending_seal}"
                if need
                else f"本条已审核但尚未挂封存凭据，审核动作重放或下次封存时按凭据 {pending_seal} 补录"
            )
        else:
            message = "本工序尚未封存档案，本条记录暂不涉及补交，按正常流程提交审核即可"
        return {
            "ok": True,
            "log_id": entry_id,
            "process": process,
            "included": False,
            "seal_no": "",
            "pending_seal_no": pending_seal,
            "补交状态": log.get("补交状态", "待补交" if seals else ""),
            "need_resubmit": bool(seals) and log.get("status") in ("待填写", "已填写"),
            "message": message,
            "process_seals": seals,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        # 返回按工序聚合的日志状态
        all_rows = store.rows(self.MODULE)
        by_process: dict[str, list] = {}
        for row in all_rows:
            process = row.get("对应工序", "")
            by_process.setdefault(process, []).append({
                "id": row.get("id"),
                "status": row.get("status"),
                "date": row.get("施工日期"),
                "seal_no": row.get("封存凭据", ""),
                "补交状态": row.get("补交状态", ""),
            })
        base["by_process"] = by_process
        return base
