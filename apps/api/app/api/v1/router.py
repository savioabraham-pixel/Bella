"""The versioned API surface.

Module routers are mounted here and nowhere else, so the full contract is readable in one
screen. Endpoints land as their phases ship — see docs/09-migration-roadmap.md.
"""

from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter()

# Phase 2 — identity, profile, relationships, consents
# api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# api_router.include_router(me.router, prefix="/me", tags=["profile"])

# Phase 3 — threads, messages, streaming, search
# api_router.include_router(threads.router, prefix="/threads", tags=["conversations"])

# Phase 4 — memory
# api_router.include_router(memories.router, prefix="/memories", tags=["memory"])

# Phase 5 — files, voice, tools
# api_router.include_router(files.router, prefix="/files", tags=["files"])
# api_router.include_router(voice.router, prefix="/voice", tags=["voice"])
# api_router.include_router(tools.router, prefix="/tools", tags=["tools"])

# Phase 7 — privacy and admin
# api_router.include_router(privacy.router, prefix="/privacy", tags=["privacy"])
# api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
