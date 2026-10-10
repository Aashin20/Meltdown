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


@router.get("/report")
async def download_report(background_tasks: BackgroundTasks) -> FileResponse:
    """Generate the current call report and return it as a PDF download."""
    temporary_file = tempfile.NamedTemporaryFile(
        prefix="sales-call-report-",
        suffix=".pdf",
        delete=False,
    )
    output_path = temporary_file.name
    temporary_file.close()

    try:
        await asyncio.to_thread(
            generate_sales_call_report,
            output_path=output_path,
            save_json=False,
        )
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        _remove_file(output_path)
        raise HTTPException(status_code=502, detail=f"Unable to generate report: {error}") from error

    background_tasks.add_task(_remove_file, output_path)
    return FileResponse(
        output_path,
        media_type="application/pdf",
        filename="sales_call_report.pdf",
    )


def _remove_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass

