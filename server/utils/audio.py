from __future__ import annotations
import argparse
import json
import os
import queue
import sys
import tempfile
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
import numpy as np
import soundcard as sc
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from google import genai
from google.genai import types

load_dotenv()

load_dotenv(
    dotenv_path=Path(__file__).resolve().parent.parent / ".env",
    override=False,
)

SAMPLE_RATE = 16_000
CAPTURE_BLOCK_FRAMES = 1_024
DEFAULT_CHUNK_SECONDS = 2
OVERLAP_SECONDS = 1
DEFAULT_OUTPUT = Path("transcript.txt")
DEFAULT_RESPONSE_OUTPUT = Path("gemini_responses.txt")
DEFAULT_LLM_MODEL = os.getenv("MODEL") or "gemini-3.5-flash-lite"
SERVER_DIR = Path(__file__).resolve().parent.parent
PRODUCT_INFO_PATH = SERVER_DIR / "product_info.json"
PRODUCT_INFO_LOCK = threading.Lock()
LLM_INTERVAL_SECONDS = 5

AudioQueue = queue.Queue[np.ndarray]
TranscriptQueue = queue.Queue[str | None]
GeminiResponseCallback = Callable[[dict[str, Any]], None]

warnings.filterwarnings(
    "ignore",
    message="data discontinuity in recording",
    category=Warning,
    module=r"soundcard\.mediafoundation",
)


def load_product_info() -> dict[str, Any]:
    """Load the product information used in Gemini requests."""
    try:
        with PRODUCT_INFO_LOCK:
            product_info = json.loads(
                PRODUCT_INFO_PATH.read_text(encoding="utf-8")
            )
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Could not read product information '{PRODUCT_INFO_PATH}': {error}"
        ) from error
    if not isinstance(product_info, dict):
        raise RuntimeError("Product information must be a JSON object.")
    return product_info

