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


@router.get("/product-info")
async def get_product_info() -> dict[str, Any]:
    return await asyncio.to_thread(load_product_info)


@router.put("/product-info")
async def update_product_info(
    product_info: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    await asyncio.to_thread(save_product_info, product_info)
    return product_info


@router.post("/product-info/generate")
async def generate_product_info_endpoint(
    request: ProductInfoGenerationRequest,
) -> dict[str, Any]:
    try:
        product_info = await asyncio.to_thread(generate_product_info, request.paragraph)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return product_info

