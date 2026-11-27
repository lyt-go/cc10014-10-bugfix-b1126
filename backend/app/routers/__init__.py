"""业务路由汇总：跨模块固定路径先注册，通用资源动态路由后注册。"""
from __future__ import annotations

from app.routers import cross_module, resource

# cross_module 的 /api/cross-module/... 固定路径必须排在 /api/{module} 动态路由之前，
# 否则会被动态路径抢先匹配。
ROUTERS = [cross_module, resource]
