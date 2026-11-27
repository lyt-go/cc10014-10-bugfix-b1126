"""跨模块联动查询路由：必须先于通用资源路由注册，避免 /{module} 动态路径抢匹配。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.services import service_for

router = APIRouter(prefix="/api/cross-module", tags=["跨模块联动"])


@router.get("/seal-boundary/restoration_log/{entry_id}")
def seal_boundary(entry_id: int) -> dict[str, Any]:
    """封存边界：工匠重新打开修缮日志时，确认本条是否已纳入档案、下次是否需补交。"""
    service = service_for("restoration_log")
    return service.seal_boundary(entry_id)


@router.get("/archive-check")
def archive_check(
    archive_id: int | None = Query(default=None),
    seal_no: str | None = Query(default=None),
) -> dict[str, Any]:
    """台账核对：交班清单与档案台账按同一封存凭据核对一致性。"""
    service = service_for("archive_mgmt")
    return service.cross_check(archive_id=archive_id, seal_no=seal_no)


@router.get("/snapshot")
def cross_module_snapshot(
    modules: str = Query(default=""),
) -> dict[str, Any]:
    """返回多个模块的状态快照，供前端跨模块面板使用。"""
    from app.store import store
    module_list = [m.strip() for m in modules.split(",") if m.strip()] if modules else []
    if not module_list:
        module_list = store.module_names()
    return store.cross_module_snapshot(module_list)


@router.get("/dependent-status/{module}/{entry_id}")
def get_dependent_status(module: str, entry_id: int) -> dict[str, Any]:
    """获取指定条目的上下游跨模块依赖状态。"""
    from fastapi import HTTPException

    from app.store import store

    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"条目 {entry_id} 在 {module} 中不存在")

    result: dict[str, Any] = {
        "module": module,
        "entry_id": entry_id,
        "entry": entry,
        "upstream": [],
        "downstream": [],
    }

    if module == "work_sequence":
        process = entry.get("工序名称", "")
        prereq = entry.get("前置工序", "")
        for ws in store.rows("work_sequence"):
            if ws.get("工序名称", "") == prereq:
                result["upstream"].append({"module": "work_sequence", "id": ws.get("id"),
                    "name": ws.get("工序名称"), "status": ws.get("status")})
        for safety in store.rows("safety_briefing"):
            if safety.get("交底工序", "") == process:
                result["upstream"].append({"module": "safety_briefing", "id": safety.get("id"),
                    "name": safety.get("交底工序"), "status": safety.get("status")})
        for qi in store.rows("quality_inspect"):
            if qi.get("检查工序", "") == process:
                result["downstream"].append({"module": "quality_inspect", "id": qi.get("id"),
                    "name": qi.get("检查工序"), "status": qi.get("status")})
        for ar in store.rows("acceptance_record"):
            if ar.get("验收工序", "") == process:
                result["downstream"].append({"module": "acceptance_record", "id": ar.get("id"),
                    "name": ar.get("验收工序"), "status": ar.get("status")})
    elif module == "quality_inspect":
        process = entry.get("检查工序", "")
        for ar in store.rows("acceptance_record"):
            if ar.get("验收工序", "") == process:
                result["downstream"].append({"module": "acceptance_record", "id": ar.get("id"),
                    "name": ar.get("验收工序"), "status": ar.get("status")})
    elif module == "change_order":
        process = entry.get("影响工序", "")
        for sched in store.rows("construction_schedule"):
            if sched.get("计划工序", "") == process:
                result["downstream"].append({"module": "construction_schedule", "id": sched.get("id"),
                    "name": sched.get("计划工序"), "status": sched.get("status")})
    elif module == "acceptance_record":
        process = entry.get("验收工序", "")
        for qi in store.rows("quality_inspect"):
            if qi.get("检查工序", "") == process:
                result["upstream"].append({"module": "quality_inspect", "id": qi.get("id"),
                    "name": qi.get("检查工序"), "status": qi.get("status")})
    elif module == "construction_schedule":
        process = entry.get("计划工序", "")
        for co in store.rows("change_order"):
            if co.get("影响工序", "") == process:
                result["upstream"].append({"module": "change_order", "id": co.get("id"),
                    "name": co.get("变更事项"), "status": co.get("status")})

    return result


@router.get("/dependency-graph")
def dependency_graph() -> dict[str, Any]:
    """返回跨模块依赖关系图。"""
    from app.services import SERVICE_INSTANCES

    graph: dict[str, Any] = {"nodes": [], "edges": []}
    for mod_name, service in SERVICE_INSTANCES.items():
        snapshot = service.domain_snapshot()
        graph["nodes"].append({
            "module": mod_name,
            "entity": snapshot.get("entity", ""),
            "count": snapshot.get("count", 0),
            "abnormal": snapshot.get("abnormal", 0),
            "by_status": snapshot.get("by_status", {}),
        })

    graph["edges"] = [
        {"from": "building_survey", "to": "restoration_plan", "label": "勘查输入方案"},
        {"from": "restoration_plan", "to": "material_verify", "label": "方案工艺约束核验"},
        {"from": "restoration_plan", "to": "expert_review", "label": "专家评审方案"},
        {"from": "expert_review", "to": "restoration_plan", "label": "汇总意见通过方案"},
        {"from": "restoration_plan", "to": "craftsman_assign", "label": "方案确定工匠"},
        {"from": "safety_briefing", "to": "work_sequence", "label": "安全交底==发布前提"},
        {"from": "safety_briefing", "to": "construction_schedule", "label": "安全交底==排期前提"},
        {"from": "craftsman_assign", "to": "work_sequence", "label": "工匠到岗==施工前提"},
        {"from": "work_sequence", "to": "construction_schedule", "label": "工序编排驱动排期"},
        {"from": "work_sequence", "to": "quality_inspect", "label": "工序完成==质量检查"},
        {"from": "quality_inspect", "to": "acceptance_record", "label": "检查通过触发验收"},
        {"from": "acceptance_record", "to": "restoration_log", "label": "验收签署归档日志"},
        {"from": "acceptance_record", "to": "work_sequence", "label": "验收签署完成工序"},
        {"from": "acceptance_record", "to": "archive_mgmt", "label": "验收签署触发归档"},
        {"from": "change_order", "to": "construction_schedule", "label": "变更签认影响排期"},
        {"from": "change_order", "to": "funding_use", "label": "变更费用追加支出"},
        {"from": "construction_schedule", "to": "completion_summary", "label": "全部排期完成==竣工"},
        {"from": "material_verify", "to": "work_sequence", "label": "材料签认解锁工序"},
        {"from": "restoration_log", "to": "work_sequence", "label": "全部日志归档==完成"},
    ]
    return graph
