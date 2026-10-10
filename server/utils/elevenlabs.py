from __future__ import annotations
import io
import json
import queue
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request
import numpy as np
from .audio import (
    OVERLAP_SECONDS,
    SAMPLE_RATE,
    AudioQueue,
    DEFAULT_RESPONSE_OUTPUT,
    TranscriptQueue,
    append_transcript,
    capture_audio,
    clear_transcript,
    run_gemini_loop,
)


ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_MODEL = "scribe_v1"


def _wav_bytes(audio: np.ndarray) -> bytes:
    """Encode normalized mono samples as a 16-bit PCM WAV file."""
    samples = np.clip(audio, -1.0, 1.0)
    pcm = (samples * 32767.0).astype(np.int16)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm.tobytes())
    return output.getvalue()


def transcribe_chunk_elevenlabs(
    api_key: str,
    audio: np.ndarray,
    language: str | None,
) -> str:
    """Transcribe one audio chunk with ElevenLabs Speech to Text."""
    wav_data = _wav_bytes(audio)
    boundary = "----LeadFlowElevenLabsBoundary"
    fields = [("model_id", ELEVENLABS_MODEL)]
    if language:
        fields.append(("language_code", language))

    body = bytearray()
    for name, value in fields:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
    )
    body.extend(b"Content-Type: audio/wav\r\n\r\n")
    body.extend(wav_data)
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    http_request = request.Request(
        ELEVENLABS_STT_URL,
        data=bytes(body),
        headers={
            "xi-api-key": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=30) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"ElevenLabs transcription failed ({exc.code}): {details or exc.reason}"
        ) from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"Could not reach ElevenLabs Speech to Text: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("ElevenLabs returned an invalid JSON response.") from exc

    text = response_data.get("text")
    if not isinstance(text, str):
        raise RuntimeError("ElevenLabs response did not include transcription text.")
    return text.strip()


def transcribe_live_elevenlabs(
    api_key: str,
    microphone: Any,
    speaker: Any,
    output_path: Path,
    chunk_seconds: int,
    language: str | None,
    gemini_client: Any,
    gemini_model: str,
    base_prompt: str,
    on_gemini_response: Any = None,
    external_stop_event: threading.Event | None = None,
    response_output_path: Path = DEFAULT_RESPONSE_OUTPUT,
) -> None:
    """Run the existing capture/Gemini pipeline with ElevenLabs transcription."""
    clear_transcript(output_path)
    clear_transcript(response_output_path)
    chunk_frames = SAMPLE_RATE * chunk_seconds
    overlap_frames = SAMPLE_RATE * OVERLAP_SECONDS
    microphone_queue: AudioQueue = queue.Queue(maxsize=1)
    system_queue: AudioQueue = queue.Queue(maxsize=1)
    stop_event = external_stop_event or threading.Event()
    transcript_queue: TranscriptQueue = queue.Queue()
    llm_thread = threading.Thread(
        target=run_gemini_loop,
        args=(
            gemini_client,
            gemini_model,
            base_prompt,
            transcript_queue,
            stop_event,
            on_gemini_response,
            response_output_path,
        ),
        name="gemini-elevenlabs-sales-copilot",
        daemon=True,
    )
    llm_thread.start()

    capture_threads = [
        threading.Thread(
            target=capture_audio,
            args=(microphone, microphone_queue, stop_event, chunk_frames, "Microphone"),
            daemon=True,
        ),
        threading.Thread(
            target=capture_audio,
            args=(speaker, system_queue, stop_event, chunk_frames, "System audio"),
            daemon=True,
        ),
    ]
    for thread in capture_threads:
        thread.start()

    previous_chunks = {
        "Microphone": np.empty(0, dtype=np.float32),
        "System audio": np.empty(0, dtype=np.float32),
    }

    def transcribe_source(
        source_and_chunk: tuple[str, np.ndarray],
    ) -> tuple[str, str, np.ndarray]:
        source_name, chunk = source_and_chunk
        audio = np.concatenate((previous_chunks[source_name], chunk))
        text = transcribe_chunk_elevenlabs(api_key, audio, language)
        return source_name, text, chunk

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            while not stop_event.is_set():
                chunks: list[tuple[str, np.ndarray]] = []
                try:
                    chunks.append(("Microphone", microphone_queue.get(timeout=0.25)))
                except queue.Empty:
                    pass
                try:
                    chunks.append(("System audio", system_queue.get_nowait()))
                except queue.Empty:
                    pass
                if not chunks:
                    continue
                for source_name, text, chunk in executor.map(transcribe_source, chunks):
                    if text:
                        append_transcript(output_path, source_name, text)
                        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
                        transcript_queue.put(f"[{timestamp}] [{source_name}] {text}")
                    previous_chunks[source_name] = chunk[-overlap_frames:]
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
    finally:
        stop_event.set()
        transcript_queue.put(None)
        llm_thread.join(timeout=10)
        for thread in capture_threads:
            thread.join(timeout=2)
