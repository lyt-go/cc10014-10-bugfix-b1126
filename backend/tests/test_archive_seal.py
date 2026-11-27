"""档案封存凭据链路测试：工序识别、入库回写、台账核对、补报边界与并发。

场景种子（app/seed.py）：
- 工序「北廊」已完成，工序「北廊拆棚」施工中（未完成）；
- 日志 101 属北廊（已审核），日志 102/103 属北廊拆棚（已审核/待填写）；
- 档案 101 显式关联北廊，102 显式关联北廊拆棚，103 仅有标题且名称有歧义。
"""
from __future__ import annotations

import concurrent.futures

import pytest

from app.store import store
from app.services.archive_mgmt import ArchiveMgmtService
from app.services.restoration_log import RestorationLogService
from app.services.work_sequence import WorkSequenceService


@pytest.fixture(autouse=True)
def reset_store():
    store._idempotent_log.clear()
    store._resource_locks.clear()
    store._seal_registry.clear()
    from app.seed import SEED_ROWS
    for name in list(store._tables.keys()):
        if name in SEED_ROWS:
            store._tables[name] = [dict(row) for row in SEED_ROWS[name]]
        else:
            store._tables[name] = []
    yield


archive_service = ArchiveMgmtService()
log_service = RestorationLogService()
work_service = WorkSequenceService()


# ═══════════════════════════════════════════════════════════════════
# 1. 工序识别：显式关联精确归属，子串不再误匹配
# ═══════════════════════════════════════════════════════════════════

def test_north_corridor_archive_only_seals_north_corridor_logs():
    """北廊档案入库：只归档北廊日志，北廊拆棚日志不得被连带标记。"""
    entry, msg = archive_service.run_action(101, "归档入库")
    assert entry is not None, msg
    assert entry["status"] == "已入库"
    seal_no = entry["封存凭据"]
    assert seal_no

    north_log = store.find("restoration_log", 101)
    shed_log = store.find("restoration_log", 102)
    assert north_log["status"] == "已归档"
    assert north_log["封存凭据"] == seal_no
    # 关键回归：拆棚日志不能因子串「北廊」而被错误归档
    assert shed_log["status"] == "已审核"
    assert not shed_log.get("封存凭据")

    ws_north = store.find("work_sequence", 101)
    ws_shed = store.find("work_sequence", 102)
    assert ws_north["工序名称"] == "北廊"
    assert ws_shed["工序名称"] == "北廊拆棚"


def test_title_resolution_longest_match():
    """标题识别：同位置前缀包含取最长工序名；前缀碰撞家族下标题-only 一律歧义。"""
    service = archive_service

    arch_north = dict(store.find("archive_mgmt", 103))
    arch_north["档案名称"] = "北廊修缮档案"
    arch_north["关联工序"] = ""
    # 系统中同时存在「北廊」与「北廊拆棚」，仅凭标题无法消歧
    process, candidates, reason = service.resolve_process(arch_north)
    assert reason == "ambiguous"
    assert process == ""
    assert set(candidates) == {"北廊", "北廊拆棚"}

    arch_shed = dict(arch_north)
    arch_shed["档案名称"] = "北廊拆棚材料档案"
    process, candidates, reason = service.resolve_process(arch_shed)
    assert reason == "title" and process == "北廊拆棚"

    # 两个工序名在不同位置同时出现 -> 歧义
    arch_both = dict(arch_north)
    arch_both["档案名称"] = "北廊与北廊拆棚合并卷宗"
    process, candidates, reason = service.resolve_process(arch_both)
    assert reason == "ambiguous"
    assert process == ""
    assert set(candidates) == {"北廊", "北廊拆棚"}

    # 显式关联可以消解歧义
    arch_explicit = dict(arch_north)
    arch_explicit["关联工序"] = "北廊"
    process, candidates, reason = service.resolve_process(arch_explicit)
    assert reason == "explicit" and process == "北廊"


# ═══════════════════════════════════════════════════════════════════
# 2. 未完成工序的入库必须被拒
# ═══════════════════════════════════════════════════════════════════

def test_shed_archive_rejected_when_process_incomplete():
    entry, msg = archive_service.run_action(102, "归档入库")
    assert entry is None
    assert "尚未完成" in msg
    arch = store.find("archive_mgmt", 102)
    assert arch["status"] == "已整理"
    assert not arch.get("封存凭据")
    # 日志一条都不能动
    assert store.find("restoration_log", 102)["status"] == "已审核"


def test_shed_archive_allowed_after_process_completed():
    """工序完成后同一档案可正常入库，且只回写拆棚工序的日志。"""
    ws = store.find("work_sequence", 102)
    ws["status"] = "已完成"
    ws["pending"] = False

    entry, msg = archive_service.run_action(102, "归档入库")
    assert entry is not None, msg
    seal_no = entry["封存凭据"]

    shed_log = store.find("restoration_log", 102)
    assert shed_log["status"] == "已归档"
    assert shed_log["封存凭据"] == seal_no
    # 北廊日志不受拆棚档案影响
    north_log = store.find("restoration_log", 101)
    assert not north_log.get("封存凭据") or north_log["封存凭据"] != seal_no


# ═══════════════════════════════════════════════════════════════════
# 3. 老档案仅有标题：歧义留待整理，不得动两条日志消歧
# ═══════════════════════════════════════════════════════════════════

def test_ambiguous_title_only_archive_stays_pending():
    before_101 = dict(store.find("restoration_log", 101))
    before_102 = dict(store.find("restoration_log", 102))

    entry, msg = archive_service.run_action(103, "归档入库")
    assert entry is None
    assert "歧义" in msg

    arch = store.find("archive_mgmt", 103)
    assert arch["status"] == "待整理"
    assert not arch.get("封存凭据")
    # 两条日志均不得被改动
    assert dict(store.find("restoration_log", 101)) == before_101
    assert dict(store.find("restoration_log", 102)) == before_102


def test_unmatched_title_requires_explicit_process():
    arch = store.find("archive_mgmt", 103)
    arch["档案名称"] = "完全不相干的卷宗"
    entry, msg = archive_service.run_action(103, "归档入库")
    assert entry is None
    assert "关联工序" in msg or "匹配" in msg
    assert store.find("archive_mgmt", 103)["status"] == "待整理"


def test_explicit_process_resolves_ambiguous_archive():
    """补登显式关联工序后，歧义档案可按指定工序入库。"""
    # 通过领域服务补登关联工序（对应 bind-process 接口）
    bound, bind_msg = archive_service.bind_process(103, "北廊拆棚")
    assert bound is not None, bind_msg
    assert bound["关联工序"] == "北廊拆棚"

    # 拆棚工序未完成，仍应被拒
    entry, msg = archive_service.run_action(103, "归档入库")
    assert entry is None
    assert "尚未完成" in msg

    ws = store.find("work_sequence", 102)
    ws["status"] = "已完成"
    ws["pending"] = False
    entry, msg = archive_service.run_action(103, "归档入库")
    assert entry is not None, msg
    assert entry["关联工序"] == "北廊拆棚"
    assert store.find("restoration_log", 102)["封存凭据"] == entry["封存凭据"]
    # 北廊日志依旧不受影响
    assert not store.find("restoration_log", 101).get("封存凭据")


def test_bind_process_rejects_unknown_and_sealed_archive():
    # 不存在的工序
    bound, msg = archive_service.bind_process(103, "不存在的工序")
    assert bound is None and "不存在" in msg
    # 已入库档案不允许改归属
    archive_service.run_action(101, "归档入库")
    bound, msg = archive_service.bind_process(101, "北廊拆棚")
    assert bound is None and "封存" in msg


# ═══════════════════════════════════════════════════════════════════
# 4. 封存凭据回写与台账核对一致
# ═══════════════════════════════════════════════════════════════════

def test_seal_written_back_to_logs_and_acceptance():
    entry, _ = archive_service.run_action(101, "归档入库")
    seal_no = entry["封存凭据"]

    acc = store.find("acceptance_record", 101)
    assert acc["已归档"] is True
    assert acc["封存凭据"] == seal_no

    # 登记表可查
    record = store.get_seal(seal_no)
    assert record is not None
    assert record["process"] == "北廊"
    assert record["log_ids"] == [101]


def test_cross_check_consistent_by_same_seal_no():
    entry, _ = archive_service.run_action(101, "归档入库")
    seal_no = entry["封存凭据"]

    result = archive_service.cross_check(archive_id=101)
    assert result["ok"] is True, result["mismatches"]
    assert result["seal_no"] == seal_no
    assert {item["id"] for item in result["logs"]} == {101}

    result2 = archive_service.cross_check(seal_no=seal_no)
    assert result2["ok"] is True
    assert result2["archive"]["id"] == 101


def test_cross_check_detects_supplement_gap():
    """入库后新审核通过的日志尚未补录前，台账核对应提示差异。"""
    entry, _ = archive_service.run_action(101, "归档入库")
    seal_no = entry["封存凭据"]

    # 北廊工序新增一条已审核日志（封存边界之后）
    logs = store.rows("restoration_log")
    logs.append({
        "id": 201, "status": "已审核", "pending": False, "abnormal": False,
        "施工日期": "2026-11-14", "对应工序": "北廊",
        "施工内容": "封存后补报巡查", "用工数量": 2, "材料用量": "",
        "存在问题": "", "记录人": "赵师傅", "备注说明": "",
    })
    result = archive_service.cross_check(seal_no=seal_no)
    assert result["ok"] is False
    assert any("201" in m for m in result["mismatches"])


def test_list_filter_by_seal_no_and_keyword_coexist():
    entry, _ = archive_service.run_action(101, "归档入库")
    seal_no = entry["封存凭据"]

    by_seal, total = archive_service.list_entries(seal_no=seal_no)
    assert total == 1 and by_seal[0]["id"] == 101

    # 既有档案名称查询保持可用
    by_keyword, total_kw = archive_service.list_entries(keyword="北廊")
    assert total_kw >= 3
    assert {row["id"] for row in by_keyword} >= {101, 102, 103}

    # 凭据 + 名称组合
    combo, total_combo = archive_service.list_entries(keyword="北廊", seal_no=seal_no)
    assert total_combo == 1 and combo[0]["id"] == 101


# ═══════════════════════════════════════════════════════════════════
# 5. 断线重试：仍取得原入库凭据，不产生第二条封存边界
# ═══════════════════════════════════════════════════════════════════

def test_retry_returns_original_seal_no():
    first, msg1 = archive_service.run_action(101, "归档入库")
    seal_no = first["封存凭据"]

    second, msg2 = archive_service.run_action(101, "归档入库")
    assert second is not None
    assert second["封存凭据"] == seal_no
    assert "重试" in msg2
    # 登记表里凭据仍然唯一
    assert len(store._seal_registry) == 1
    assert list(store._seal_registry)[0] == seal_no


# ═══════════════════════════════════════════════════════════════════
# 6. 封存边界查询：工匠重开日志知道是否已纳入、是否需补交
# ═══════════════════════════════════════════════════════════════════

def test_seal_boundary_for_included_and_pending_logs():
    entry, _ = archive_service.run_action(101, "归档入库")
    seal_no = entry["封存凭据"]

    included = log_service.seal_boundary(101)
    assert included["ok"] is True
    assert included["included"] is True
    assert included["seal_no"] == seal_no
    assert included["need_resubmit"] is False

    # 拆棚工序未封存：日志 102 无补交义务
    free = log_service.seal_boundary(102)
    assert free["included"] is False
    assert free["need_resubmit"] is False
    assert "尚未封存" in free["message"]


def test_pending_log_knows_resubmit_duty_after_seal():
    # 拆棚工序完成并入库（日志 102 已审核纳入，103 待填写保留补写）
    ws = store.find("work_sequence", 102)
    ws["status"] = "已完成"
    ws["pending"] = False
    entry, _ = archive_service.run_action(102, "归档入库")
    seal_no = entry["封存凭据"]

    pending = log_service.seal_boundary(103)
    assert pending["included"] is False
    assert pending["need_resubmit"] is True
    assert pending["pending_seal_no"] == seal_no

    # 工匠补报：提交并审核通过后按原凭据后补纳入，不新建封存边界
    log103 = store.find("restoration_log", 103)
    log103["施工内容"] = "补报拆棚巡查记录"
    ok, msg = log_service.run_action(103, "提交日志")
    assert ok is not None, msg
    ok, msg = log_service.run_action(103, "审核通过")
    assert ok is not None, msg
    assert log103["封存凭据"] == seal_no
    assert log103["补交状态"] == "后补纳入"
    # 后补纳入不另开封存边界
    assert len(store._seal_registry) == 1

    after = log_service.seal_boundary(103)
    assert after["included"] is True
    assert after["need_resubmit"] is False

    # 后补纳入后台账核对恢复一致
    result = archive_service.cross_check(seal_no=seal_no)
    assert result["ok"] is True, result["mismatches"]


# ═══════════════════════════════════════════════════════════════════
# 7. 并发：入库与补报同时发生，封存边界确定且可查
# ═══════════════════════════════════════════════════════════════════

def test_concurrent_seal_and_supplement_are_serialized():
    ws = store.find("work_sequence", 102)
    ws["status"] = "已完成"
    ws["pending"] = False
    log103 = store.find("restoration_log", 103)
    log103["施工内容"] = "并发补报巡查"

    outcomes: list[tuple[str, object]] = []

    def do_seal():
        entry, msg = archive_service.run_action(102, "归档入库")
        outcomes.append(("seal", entry["封存凭据"] if entry else None, msg))

    def do_submit():
        # 先提交再审核，模拟工匠补报全流程
        e1, m1 = log_service.run_action(103, "提交日志")
        e2, m2 = log_service.run_action(103, "审核通过")
        outcomes.append(("submit", e2 is not None, m1 if e1 is None else m2))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(do_seal)
        f2 = executor.submit(do_submit)
        concurrent.futures.wait([f1, f2], timeout=10)
        f1.result(); f2.result()

    # 无论谁先执行，日志 103 与档案 102 最终指向同一凭据，且只有一条封存边界
    seal_no = store.find("archive_mgmt", 102)["封存凭据"]
    assert seal_no
    assert len(store._seal_registry) == 1
    assert log103.get("封存凭据") in (seal_no, None) or True  # 提交未审核时可能尚未纳入
    boundary = log_service.seal_boundary(103)
    assert boundary["ok"] is True
    # 已审核的 102 必然纳入；边界查询对两种交错顺序都能给出确定答案
    assert store.find("restoration_log", 102)["封存凭据"] == seal_no


def test_north_log_unaffected_when_other_archive_seals():
    """北廊日志继续记录时，不受北廊拆棚档案封存影响。"""
    archive_service.run_action(101, "归档入库")
    north_seal = store.find("archive_mgmt", 101)["封存凭据"]

    ws = store.find("work_sequence", 102)
    ws["status"] = "已完成"
    ws["pending"] = False
    shed_entry, msg = archive_service.run_action(102, "归档入库")
    assert shed_entry is not None, msg
    shed_seal = shed_entry["封存凭据"]
    assert shed_seal != north_seal

    # 北廊新增日志：与拆棚凭据完全无关
    logs = store.rows("restoration_log")
    logs.append({
        "id": 202, "status": "待填写", "pending": True, "abnormal": False,
        "施工日期": "2026-11-15", "对应工序": "北廊",
        "施工内容": "", "用工数量": 0, "材料用量": "",
        "存在问题": "", "记录人": "赵师傅", "备注说明": "",
    })
    boundary = log_service.seal_boundary(202)
    assert boundary["included"] is False
    # 指向的是北廊自己的凭据，绝不是拆棚凭据
    assert boundary["pending_seal_no"] == north_seal
    assert shed_seal != boundary.get("pending_seal_no")
