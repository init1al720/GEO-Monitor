"""v1 路由聚合：新模块在这里挂一行，main.py 无需改动。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import auth, example, health, projects

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(example.router)
