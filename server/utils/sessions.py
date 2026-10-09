from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel
from google import genai

from .audio import (
    DEFAULT_CHUNK_SECONDS,
    DEFAULT_LLM_MODEL,
    DEFAULT_OUTPUT,
    DEFAULT_RESPONSE_OUTPUT,
    choose_microphone,
    choose_speaker,
    load_base_prompt,
    transcribe_live,
)
from .elevenlabs import transcribe_live_elevenlabs


class AssistantSession:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.responses: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    async def start(self, options: dict[str, Any]) -> None:
        if self.running:
            return

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to the environment before starting."
            )

        model_name = str(options.get("model", "base"))
        device = str(options.get("device", "cpu"))
        compute_type = str(
            options.get("compute_type", "int8" if device == "cpu" else "float16")
        )
        chunk_seconds = int(options.get("chunk_seconds", DEFAULT_CHUNK_SECONDS))
        if chunk_seconds not in (1, 2, 3, 4):
            raise ValueError("chunk_seconds must be between 1 and 4.")

        microphone_name = options.get("mic")
        speaker_name = options.get("speaker")
        llm_model = str(options.get("llm_model", DEFAULT_LLM_MODEL))
        output_path = Path(str(options.get("output", DEFAULT_OUTPUT)))
        response_output_path = Path(
            str(options.get("response_output", DEFAULT_RESPONSE_OUTPUT))
        )

        microphone, speaker, model, client, prompt = await asyncio.to_thread(
            self._load_dependencies,
            api_key,
            model_name,
            device,
            compute_type,
            microphone_name,
            speaker_name,
        )
        self.stop_event.clear()
        self.worker = threading.Thread(
            target=transcribe_live,
            args=(
                model,
                microphone,
                speaker,
                output_path,
                chunk_seconds,
                options.get("language"),
                client,
                llm_model,
                prompt,
            ),
            kwargs={
                "on_gemini_response": self._publish_response,
                "external_stop_event": self.stop_event,
                "response_output_path": response_output_path,
            },
            name="voice-assistant-session",
            daemon=True,
        )
        self.worker.start()

    @staticmethod
    def _load_dependencies(
        api_key: str,
        model_name: str,
        device: str,
        compute_type: str,
        microphone_name: str | None,
        speaker_name: str | None,
    ) -> tuple[Any, Any, WhisperModel, genai.Client, str]:
        microphone = choose_microphone(microphone_name)
        speaker = choose_speaker(speaker_name)
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )
        return (
            microphone,
            speaker,
            model,
            genai.Client(api_key=api_key),
            load_base_prompt(),
        )

    def _publish_response(self, response: dict[str, Any]) -> None:
        self.loop.call_soon_threadsafe(self.responses.put_nowait, response)

    async def stop(self) -> None:
        if self.worker is None:
            return
        self.stop_event.set()
        await asyncio.to_thread(self.worker.join, 12)
        if self.worker.is_alive():
            raise RuntimeError("The audio worker did not stop within 12 seconds.")
        self.worker = None

