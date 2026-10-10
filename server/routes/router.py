from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..report import generate_sales_call_report
from ..utils.audio import generate_product_info, load_product_info, save_product_info
from ..utils.sessions import AssistantSession, ElevenLabsAssistantSession

router = APIRouter()


class ProductInfoGenerationRequest(BaseModel):
    paragraph: str = Field(..., min_length=1, max_length=20_000)


async def send_responses(websocket: WebSocket, session: AssistantSession) -> None:
    while True:
        response = await session.responses.get()
        await websocket.send_json({"type": "ai_response", "data": response})


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

