"""档案归档领域服务：整理工序完成后联动归档，入库后按封存凭据锁定关联记录。

工序识别规则（修复历史上「北廊」子串误匹配「北廊拆棚」的问题）：
1. 档案显式填写「关联工序」时，只认该工序，必须与工序编排中的工序卡精确同名；
2. 仅有档案名称时，对工序名称做最长匹配（短名仅作为长名一部分出现不算独立命中）：
   - 独立命中恰好一个工序 -> 按该工序处理；
   - 独立命中的短工序名在系统中还存在以它为前缀的其它工序（如「北廊」与
     「北廊拆棚」并存），或标题同时出现多个互不包含的工序名 -> 存在歧义，
     档案留在「待整理」，不得通过改动任何日志来消除歧义；
   - 一个都匹配不到 -> 提示补登关联工序，同样留在「待整理」。
入库成功后生成封存凭据，回写到验收记录与修缮日志（交班清单），
档案台账与交班清单凭同一封存凭据核对一致。
"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ArchiveMgmtService(BaseModuleService):
    MODULE = "archive_mgmt"
    ENTITY = "修缮档案"
    OPERATION_TERM = "整理归档"
    LIST_FIELDS = ["档案名称", "档案类别", "形成日期", "保管期限", "密级等级", "归档人", "归档状态", "备注说明"]
    REQUIRED_FIELDS = ["档案名称", "档案类别", "形成日期"]
    STATUS_ORDER = ["待整理", "已整理", "已入库", "已借阅"]
    ACTION_RULES = {"整理归档": "已整理", "归档入库": "已入库", "借阅档案": "已借阅"}
    NEGATIVE_ACTIONS = []
    SEAL_PREFIX = "SEAL"

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing = super().create_entry(values)
        if entry is not None:
            entry["关联工序"] = str(values.get("关联工序", "") or "").strip()
        return entry, missing

    def bind_process(self, entry_id: int, process: str) -> tuple[dict[str, Any] | None, str]:
        """为档案补登/修正显式关联工序（仅入库前允许，入库后凭据边界不可改）。"""
        process = str(process or "").strip()
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"
        if entry.get("封存凭据"):
            return None, "档案已入库封存，关联工序不可修改"
        if not process:
            return None, "关联工序不能为空"
        valid = {str(row.get("工序名称", "") or "") for row in store.rows("work_sequence")}
        if process not in valid:
            return None, f"关联工序「{process}」在工序编排中不存在，请核对后重新指定"
        entry["关联工序"] = process
        return entry, f"档案已关联工序「{process}」，可重新发起入库"

    # ── 工序识别 ──
    def resolve_process(self, entry: dict[str, Any]) -> tuple[str, list[str], str]:
        """识别档案归属工序。

        返回 (工序名称, 命中的候选工序列表, 说明)。
        显式关联工序优先；否则按档案名称做最长匹配：
        - 同一位置存在前缀包含关系时取最长工序名（「北廊拆棚」优先于「北廊」），
          因此「北廊拆棚材料档案」只命中拆棚，「北廊修缮档案」只命中北廊；
        - 名称中同时出现两个互不包含的工序名 -> 歧义，留待整理；
        - 一个都匹配不到 -> 提示补登关联工序。
        """
        arch_name = str(entry.get("档案名称", "") or "")
        explicit = str(entry.get("关联工序", "") or "").strip()
        process_names = [str(row.get("工序名称", "") or "") for row in store.rows("work_sequence")]
        process_names = [name for name in process_names if name]

        if explicit:
            if explicit in process_names:
                return explicit, [explicit], "explicit"
            return "", [], f"关联工序「{explicit}」在工序编排中不存在，请核对后重新登记"

        # 每个命中位置只保留最长工序名：短工序名若仅作为长工序名的一部分出现
        # （如「北廊」只出现在「北廊拆棚材料档案」里），不算独立命中。
        chosen_at: dict[int, str] = {}
        for name in process_names:
            cursor = 0
            while True:
                idx = arch_name.find(name, cursor)
                if idx < 0:
                    break
                current = chosen_at.get(idx)
                if current is None or len(name) > len(current):
                    chosen_at[idx] = name
                cursor = idx + len(name)

        # 独立命中：在某个位置本身就是最长匹配的工序名
        independent: list[str] = []
        for name in chosen_at.values():
            if name not in independent:
                independent.append(name)

        # 标题-only 档案没有显式归属：若标题独立命中了某个短工序名，而系统里还
        # 存在以它为前缀的其它工序（如独立出现的「北廊」对应「北廊拆棚」），
        # 仅凭标题无法判断档案究竟属于哪一道工序，按歧义处理、留待整理，
        # 不得擅自改动两条日志来消歧。
        collision_hits = sorted(
            name for name in independent
            if any(other != name and other.startswith(name) for other in process_names)
        )
        if collision_hits:
            family = []
            for name in collision_hits:
                family.extend(other for other in process_names
                              if other == name or other.startswith(name))
            return "", sorted(set(family)), "ambiguous"

        unique = independent

        if len(unique) == 1:
            return unique[0], unique, "title"
        if len(unique) > 1:
            return "", unique, "ambiguous"
        return "", [], "unmatched"

    # ── 入库前置校验 ──
    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "归档入库":
            process, candidates, reason = self.resolve_process(entry)
            if reason == "explicit" or reason == "title":
                # 入库前确认关联工序是否已完成（未完成工序的入库必须拒绝）
                for ws_row in store.rows("work_sequence"):
                    if ws_row.get("工序名称", "") == process:
                        if ws_row.get("status") != "已完成":
                            return False, f"档案关联工序「{process}」尚未完成，不能入库"
                        break
            elif reason == "ambiguous":
                return False, (
                    f"档案名称同时命中多个工序（{'、'.join(candidates)}），归属存在歧义，"
                    "请在「关联工序」中明确指定后再入库，档案暂留在待整理"
                )
            else:
                hint = reason if reason != "unmatched" else "档案名称未能匹配任何工序"
                return False, f"{hint}，请补登「关联工序」后再入库，档案暂留在待整理"
        if action == "借阅档案":
            if entry.get("密级等级", "") in ("机密", "绝密"):
                return False, "高密级档案不支持借阅"
        return True, ""

    # ── 入库副作用：生成封存凭据并回写交班清单 ──
    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> list[dict]:
        effects: list[dict] = []
        if action != "归档入库":
            return effects

        process, _candidates, _reason = self.resolve_process(entry)
        seal_no = self._make_seal_no(entry)
        sealed_at = time.time()

        entry["关联工序"] = process
        entry["封存凭据"] = seal_no
        entry["封存时间"] = sealed_at

        # 验收记录回写：仅精确归属本工序的已签署记录
        for acc in store.rows("acceptance_record"):
            if acc.get("验收工序", "") == process and acc.get("status") == "已签署":
                acc["已归档"] = True
                acc["封存凭据"] = seal_no
                effects.append(dict(acc))

        # 修缮日志（交班清单）回写：仅精确归属本工序、且已审核的日志
        sealed_log_ids: list[int] = []
        for log in store.rows("restoration_log"):
            if log.get("对应工序", "") != process:
                continue
            if log.get("status") == "已审核":
                log["status"] = "已归档"
                log["pending"] = False
                log["封存凭据"] = seal_no
                log["档案ID"] = entry_id
                log["封存时间"] = sealed_at
                log["补交状态"] = "已纳入"
                sealed_log_ids.append(int(log.get("id", 0)))
                effects.append(dict(log))
            else:
                # 未到审核节点的日志不纳入本次封存，保留可补写空间
                log["待补交凭据"] = seal_no
                log["补交状态"] = "待补交"
                effects.append(dict(log))

        record = {
            "seal_no": seal_no,
            "archive_id": entry_id,
            "archive_name": entry.get("档案名称", ""),
            "process": process,
            "sealed_at": sealed_at,
            "log_ids": sealed_log_ids,
        }
        store.register_seal(record)
        return effects

    def _make_seal_no(self, entry: dict[str, Any]) -> str:
        """确定性封存凭据号：同一档案重复入库（断线重试）始终得到同一凭据。"""
        formed = str(entry.get("形成日期", "") or "")
        digits = "".join(ch for ch in formed if ch.isdigit()) or time.strftime("%Y%m%d")
        return f"{self.SEAL_PREFIX}-{digits}-{int(entry.get('id', 0)):04d}"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        # 断线重试：已入库档案重复发起入库，直接返回原入库凭据，不产生新副作用
        if action == "归档入库":
            existing = store.find(self.MODULE, entry_id)
            if existing is not None and existing.get("封存凭据"):
                return existing, (
                    f"该档案已入库，封存凭据：{existing['封存凭据']}（重试未重复入库）"
                )
        # 工序级串行：档案入库与同工序日志补报互斥，封存边界可查、不交叉
        entry = store.find(self.MODULE, entry_id)
        process_key = ""
        if entry is not None and action == "归档入库":
            process, _candidates, _reason = self.resolve_process(entry)
            process_key = f"seal-process:{process or f'archive-{entry_id}'}"
        if process_key and not store.acquire_resource(process_key):
            return None, "该工序正在处理封存/补报，请稍后重试"
        try:
            return super().run_action(entry_id, action)
        finally:
            if process_key:
                store.release_resource(process_key)

    # ── 列表：兼容档案名称查询，同时支持按封存凭据核对 ──
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        seal_no: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows, total = super().list_entries(
            filters=filters, keyword=keyword, status=status, page=1, size=1_000_000
        )
        if seal_no:
            wanted = seal_no.strip()
            rows = [row for row in rows if str(row.get("封存凭据", "") or "") == wanted]
            total = len(rows)
            start = (page - 1) * size
            rows = rows[start:start + size]
        else:
            start = (page - 1) * size
            rows = rows[start:start + size]
        return rows, total

    # ── 台账核对：档案台账与交班清单按同一封存凭据对账 ──
    def cross_check(self, *, archive_id: int | None = None, seal_no: str | None = None) -> dict[str, Any]:
        archive = None
        if archive_id is not None:
            archive = store.find(self.MODULE, archive_id)
            if archive is None:
                return {"ok": False, "message": f"修缮档案 {archive_id} 不存在"}
            seal_no = str(archive.get("封存凭据", "") or "")
        elif seal_no:
            for row in store.rows(self.MODULE):
                if str(row.get("封存凭据", "") or "") == seal_no:
                    archive = row
                    break

        if archive is None or not seal_no:
            return {"ok": False, "message": "未找到对应封存凭据，档案可能尚未入库", "seal_no": seal_no or ""}

        process = str(archive.get("关联工序", "") or "")
        registry_record = store.get_seal(seal_no)

        ledger_logs: list[dict[str, Any]] = []
        mismatches: list[str] = []
        for log in store.rows("restoration_log"):
            if log.get("对应工序", "") != process:
                continue
            log_seal = str(log.get("封存凭据", "") or "")
            if log_seal == seal_no:
                ledger_logs.append({
                    "id": log.get("id"),
                    "施工日期": log.get("施工日期"),
                    "对应工序": log.get("对应工序"),
                    "status": log.get("status"),
                    "封存凭据": log_seal,
                    "补交状态": log.get("补交状态", "已纳入"),
                })
                if log.get("status") not in ("已归档", "已审核"):
                    mismatches.append(f"日志 {log.get('id')} 已挂封存凭据但状态不是已归档/已审核")
            elif log.get("status") == "已审核":
                # 封存之后才审核通过、且未挂本凭据的日志：尚未纳入，提示需要补交到该凭据
                mismatches.append(f"日志 {log.get('id')} 已审核但未纳入封存凭据 {seal_no}，需补报归档")

        registry_ids = list(registry_record.get("log_ids", [])) if registry_record else []
        registry_ids += list(registry_record.get("supplement_log_ids", [])) if registry_record else []
        ledger_ids = [item["id"] for item in ledger_logs]
        if registry_record and sorted(registry_ids) != sorted(ledger_ids):
            mismatches.append("封存登记表与交班清单记录的日志范围不一致")

        acceptance = [
            {"id": acc.get("id"), "验收工序": acc.get("验收工序"), "封存凭据": acc.get("封存凭据", "")}
            for acc in store.rows("acceptance_record")
            if acc.get("验收工序", "") == process and str(acc.get("封存凭据", "") or "") == seal_no
        ]

        return {
            "ok": not mismatches,
            "message": "台账与交班清单核对一致" if not mismatches else "核对发现差异",
            "seal_no": seal_no,
            "archive": {"id": archive.get("id"), "档案名称": archive.get("档案名称"),
                        "关联工序": process, "status": archive.get("status")},
            "process": process,
            "logs": ledger_logs,
            "acceptance_records": acceptance,
            "registry": registry_record,
            "mismatches": mismatches,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["sealed_archives"] = [
            {"id": row.get("id"), "档案名称": row.get("档案名称"),
             "关联工序": row.get("关联工序", ""), "封存凭据": row.get("封存凭据", "")}
            for row in store.rows(self.MODULE) if row.get("封存凭据")
        ]
        return base
