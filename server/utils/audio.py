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


def save_product_info(product_info: dict[str, Any]) -> None:
    """Atomically replace the product information JSON file."""
    if not isinstance(product_info, dict):
        raise ValueError("Product information must be a JSON object.")

    with PRODUCT_INFO_LOCK:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=PRODUCT_INFO_PATH.parent,
                prefix=f"{PRODUCT_INFO_PATH.stem}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                json.dump(product_info, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write("\n")
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, PRODUCT_INFO_PATH)
        except OSError as error:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
            raise RuntimeError(
                f"Could not write product information '{PRODUCT_INFO_PATH}': {error}"
            ) from error


def generate_product_info(paragraph: str) -> dict[str, Any]:
    """Convert a product description into the product-info schema and save it."""
    paragraph = paragraph.strip()
    if not paragraph:
        raise ValueError("Product paragraph must not be empty.")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to the environment before generating product information."
        )

    schema = {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING"},
            "description": {"type": "STRING"},
            "target_customers": {"type": "ARRAY", "items": {"type": "STRING"}},
            "features": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {"type": "STRING"},
                        "description": {"type": "STRING"},
                    },
                    "required": ["name", "description"],
                },
            },
            "use_cases": {"type": "ARRAY", "items": {"type": "STRING"}},
            "pricing": {
                "type": "OBJECT",
                "properties": {
                    "currency": {"type": "STRING"},
                    "plans": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "name": {"type": "STRING"},
                                "price_per_user_per_month": {"type": "NUMBER"},
                                "billing_period": {"type": "STRING"},
                            },
                            "required": [
                                "name",
                                "price_per_user_per_month",
                                "billing_period",
                            ],
                        },
                    },
                    "discount_policy": {"type": "STRING"},
                },
                "required": ["currency", "plans", "discount_policy"],
            },
            "integrations": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {"type": "STRING"},
                        "description": {"type": "STRING"},
                    },
                    "required": ["name", "description"],
                },
            },
            "implementation": {
                "type": "OBJECT",
                "properties": {
                    "process": {"type": "STRING"},
                    "timeline": {"type": "STRING"},
                },
                "required": ["process", "timeline"],
            },
            "security": {"type": "ARRAY", "items": {"type": "STRING"}},
            "limitations": {"type": "ARRAY", "items": {"type": "STRING"}},
            "faq": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "question": {"type": "STRING"},
                        "answer": {"type": "STRING"},
                    },
                    "required": ["question", "answer"],
                },
            },
            "competitors": {"type": "ARRAY", "items": {"type": "STRING"}},
            "evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": [
            "name",
            "description",
            "target_customers",
            "features",
            "use_cases",
            "pricing",
            "integrations",
            "implementation",
            "security",
            "limitations",
            "faq",
            "competitors",
            "evidence",
        ],
    }
    prompt = (
        "Convert the following product paragraph into the requested product information schema. "
        "Use only facts stated in the paragraph. If a value is unknown, use an empty string or "
        "empty array. Do not add verification, dates, or guessed claims.\n\n"
        f"Product paragraph:\n{paragraph}"
    )
    try:
        response = genai.Client(api_key=api_key).models.generate_content(
            model=DEFAULT_LLM_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.1,
            ),
        )
        response_text = response.text
        if not response_text:
            raise ValueError("Gemini returned an empty response.")
        product_info = json.loads(response_text)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeError(f"Gemini returned invalid product information: {error}") from error

    if not isinstance(product_info, dict):
        raise RuntimeError("Gemini product information must be a JSON object.")
    save_product_info(product_info)
    return product_info


def list_devices() -> None:
    """Print available capture devices and return."""
    print("Microphones:")
    for microphone in sc.all_microphones():
        print(f"  {microphone.name}")

    print("\nSpeakers (system-audio loopback sources):")
    for speaker in sc.all_speakers():
        print(f"  {speaker.name}")


def choose_microphone(name: str | None) -> Any:
    """Resolve a requested microphone, or use the operating-system default."""
    if name:
        return sc.get_microphone(name)
    microphone = sc.default_microphone()
    if microphone is None:
        raise RuntimeError("No default microphone was found.")
    return microphone


def choose_speaker(name: str | None) -> Any:
    """Resolve a speaker and return its SoundCard loopback microphone."""
    speaker = sc.get_speaker(name) if name else sc.default_speaker()
    if speaker is None:
        raise RuntimeError("No default speaker was found.")
    return sc.get_microphone(speaker.name, include_loopback=True)


def capture_audio(
    source: Any,
    audio_queue: AudioQueue,
    stop_event: threading.Event,
    chunk_frames: int,
    source_name: str,
) -> None:
    """Capture small device buffers and publish fixed-size audio chunks."""
    while not stop_event.is_set():
        pending: list[np.ndarray] = []
        pending_frames = 0
        try:
            with source.recorder(
                samplerate=SAMPLE_RATE,
                channels=1,
                blocksize=CAPTURE_BLOCK_FRAMES,
            ) as recorder:
                while not stop_event.is_set():
                    samples = np.asarray(
                        recorder.record(numframes=CAPTURE_BLOCK_FRAMES),
                        dtype=np.float32,
                    )
                    if samples.ndim == 2:
                        samples = samples.mean(axis=1)
                    samples = np.clip(samples.reshape(-1), -1.0, 1.0)
                    if samples.size == 0:
                        continue
                    pending.append(samples)
                    pending_frames += samples.size
                    while pending_frames >= chunk_frames:
                        chunk = np.concatenate(pending)
                        chunk_to_queue = chunk[:chunk_frames]
                        remainder = chunk[chunk_frames:]
                        pending = [remainder] if remainder.size else []
                        pending_frames = remainder.size
                        try:
                            audio_queue.put_nowait(chunk_to_queue)
                        except queue.Full:
                            try:
                                audio_queue.get_nowait()
                            except queue.Empty:
                                pass
                            try:
                                audio_queue.put_nowait(chunk_to_queue)
                            except queue.Full:
                                pass
        except Exception as error:
            if stop_event.is_set():
                break
            print(
                f"{source_name} capture interrupted ({error}); "
                "retrying...",
                file=sys.stderr,
            )
            time.sleep(1)


def transcribe_chunk(
    model: WhisperModel,
    audio: np.ndarray,
    language: str | None,
) -> str:
    """Transcribe one source chunk and return its non-empty text."""
    segments, _ = model.transcribe(
        audio,
        language=language,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


def append_transcript(output_path: Path, source_name: str, text: str) -> None:
    """Print and append one timestamped, source-labelled transcription line."""
    line = (
        f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] "
        f"[{source_name}] {text}"
    )
    print(line, flush=True)
    with output_path.open("a", encoding="utf-8") as output:
        output.write(line + "\n")


def clear_transcript(output_path: Path) -> None:
    """Start a new live session with an empty transcript file."""
    output_path.write_text("", encoding="utf-8")


def append_gemini_response(output_path: Path, response: dict[str, Any]) -> None:
    """Append one timestamped Gemini response as a JSON line."""
    entry = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "response": response,
    }
    with output_path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_gemini_loop(
    client: genai.Client,
    model_name: str,
    base_prompt: str,
    transcript_queue: TranscriptQueue,
    stop_event: threading.Event,
    on_response: GeminiResponseCallback | None = None,
    response_output_path: Path | None = None,
) -> None:
    """Send new transcript batches to Gemini and retain its rolling context."""
    rolling_context: dict[str, Any] | None = None
    previous_suggestion = ""
    pending_suggestions: dict[str, Any] = {}
    suggestion_counter = 0
    pending: list[str] = []
    finished = False
    next_request_at = time.monotonic() + LLM_INTERVAL_SECONDS

    while not finished:
        remaining = max(0.0, next_request_at - time.monotonic())
        if remaining > 0:
            try:
                item = transcript_queue.get(timeout=remaining)
                if item is None:
                    finished = True
                else:
                    pending.append(item)
            except queue.Empty:
                pass

        if not finished:
            while True:
                try:
                    item = transcript_queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    finished = True
                    break
                pending.append(item)

        if not finished and time.monotonic() < next_request_at:
            continue

        if not pending:
            if stop_event.is_set():
                break
            next_request_at = time.monotonic() + LLM_INTERVAL_SECONDS
            continue

        request = {
            "transcript": "\n".join(pending),
            "context": rolling_context,
            "previous_suggestion": previous_suggestion,
            "pending_suggestions": pending_suggestions,
            "salesperson_objective": "Understand the customer's sales workflow and pain points, demonstrate how LeadFlow CRM addresses their needs, handle objections honestly, qualify their readiness to buy, and secure a product demo if there is a good fit. Do not pressure the customer or promise unsupported capabilities.",
            "product_info": load_product_info(),
        }
        pending.clear()

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=json.dumps(request, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    system_instruction=base_prompt,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            response_text = response.text
            if not response_text:
                raise ValueError("Gemini returned an empty response.")
            result = json.loads(response_text)
            if not isinstance(result, dict):
                raise ValueError("Gemini response was not a JSON object.")
            context = result.get("context")
            if not isinstance(context, dict):
                raise ValueError("Gemini response did not include a context object.")
            response_pending_suggestions = result.get("pending_suggestions")
            if response_pending_suggestions is None:
                # A missing/null field must not erase unresolved talking points.
                # Gemini may place the field in context because the prompt contains
                # both schemas, so accept that location as a compatibility fallback.
                context_pending_suggestions = context.get("pending_suggestions")
                if isinstance(context_pending_suggestions, dict):
                    response_pending_suggestions = context_pending_suggestions
                else:
                    response_pending_suggestions = pending_suggestions
            if not isinstance(response_pending_suggestions, dict):
                raise ValueError(
                    "Gemini response did not include a pending_suggestions object."
                )
            resolved_ids = result.get("resolved_suggestion_ids", [])
            if not isinstance(resolved_ids, list) or not all(
                isinstance(item, str) for item in resolved_ids
            ):
                raise ValueError(
                    "Gemini response included invalid resolved_suggestion_ids."
                )

            has_salesperson_speech = any(
                "] [Microphone] " in line
                for line in request["transcript"].split("\n")
            )
            if resolved_ids and not has_salesperson_speech:
                print(
                    f"[guard] ignored resolved ids {resolved_ids}: no salesperson speech",
                    flush=True,
                )
                resolved_ids = []
            updated_pending_suggestions = {
                suggestion_id: suggestion
                for suggestion_id, suggestion in pending_suggestions.items()
                if suggestion_id not in resolved_ids
            }
            for suggestion_id, suggestion in response_pending_suggestions.items():
                if (
                    suggestion_id not in updated_pending_suggestions
                    or updated_pending_suggestions[suggestion_id] == suggestion
                ):
                    updated_pending_suggestions[suggestion_id] = suggestion
                    continue

                # Gemini sometimes reuses ids such as "s1" for a different
                # talking point. Keep the unresolved old entry visible and
                # assign the replacement a new id instead of hiding it behind
                # the notes component's existing key.
                replacement_id = suggestion_id
                suffix = 2
                while replacement_id in updated_pending_suggestions:
                    replacement_id = f"{suggestion_id}_{suffix}"
                    suffix += 1
                updated_pending_suggestions[replacement_id] = suggestion
            # Only what Gemini explicitly resolved (and did not re-list)
            # counts as completed.
            completed_suggestions = {
                suggestion_id: pending_suggestions[suggestion_id]
                for suggestion_id in resolved_ids
                if suggestion_id in pending_suggestions
                and suggestion_id not in response_pending_suggestions
            }
            # Every suggestion shown on the orb becomes a note deterministically.
            shown = result.get("suggestion")
            if (
                result.get("suggestion_needed")
                and isinstance(shown, str)
                and shown.strip()
            ):
                shown = shown.strip()
                already = any(
                    isinstance(value, dict)
                    and str(value.get("suggestion", "")).strip() == shown
                    for value in updated_pending_suggestions.values()
                )
                if not already:
                    suggestion_counter += 1
                    updated_pending_suggestions[f"n{suggestion_counter}"] = {
                        "suggestion": shown,
                        "customer_intent": result.get("customer_intent", ""),
                        "event_type": result.get(
                            "event_type", "continue_listening"
                        ),
                    }
            rolling_context = context
            pending_suggestions = updated_pending_suggestions
            result["pending_suggestions"] = pending_suggestions
            result["completed_suggestions"] = completed_suggestions
            suggestion = result.get("suggestion", "")
            previous_suggestion = suggestion if isinstance(suggestion, str) else ""
            print(
                f"[Gemini] {json.dumps(result, ensure_ascii=False)}",
                flush=True,
            )
            if response_output_path is not None:
                append_gemini_response(response_output_path, result)
            if on_response is not None:
                on_response(result)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            print(f"Gemini response error: {error}", file=sys.stderr, flush=True)
        except Exception as error:
            print(f"Gemini request failed: {error}", file=sys.stderr, flush=True)

        next_request_at = time.monotonic() + LLM_INTERVAL_SECONDS
        if stop_event.is_set() and transcript_queue.empty():
            break


def load_base_prompt() -> str:
    """Load the Gemini system prompt located beside the application."""
    prompt_path = SERVER_DIR / "prompt.txt"
    try:
        return prompt_path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"Could not read base prompt '{prompt_path}': {error}") from error


def run_transcript_test(
    transcript_path: Path,
    gemini_client: genai.Client,
    gemini_model: str,
    base_prompt: str,
) -> None:
    """Send a text transcript through the Gemini loop without audio capture."""
    try:
        transcript = transcript_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(
            f"Could not read test transcript '{transcript_path}': {error}"
        ) from error
    if not transcript:
        raise RuntimeError(f"Test transcript '{transcript_path}' is empty.")

    transcript_queue: TranscriptQueue = queue.Queue()
    stop_event = threading.Event()
    llm_thread = threading.Thread(
        target=run_gemini_loop,
        args=(
            gemini_client,
            gemini_model,
            base_prompt,
            transcript_queue,
            stop_event,
        ),
        name="gemini-transcript-test",
    )
    llm_thread.start()
    transcript_queue.put(transcript)
    transcript_queue.put(None)
    llm_thread.join()


def transcribe_live(
    model: WhisperModel,
    microphone: Any,
    speaker: Any,
    output_path: Path,
    chunk_seconds: int,
    language: str | None,
    gemini_client: genai.Client,
    gemini_model: str,
    base_prompt: str,
    on_gemini_response: GeminiResponseCallback | None = None,
    external_stop_event: threading.Event | None = None,
    response_output_path: Path = DEFAULT_RESPONSE_OUTPUT,
) -> None:
    """Capture and transcribe each source until interrupted."""
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
        name="gemini-sales-copilot",
        daemon=True,
    )
    llm_thread.start()

    capture_threads = [
        threading.Thread(
            target=capture_audio,
            args=(
                microphone,
                microphone_queue,
                stop_event,
                chunk_frames,
                "Microphone",
            ),
            daemon=True,
        ),
        threading.Thread(
            target=capture_audio,
            args=(
                speaker,
                system_queue,
                stop_event,
                chunk_frames,
                "System audio",
            ),
            daemon=True,
        ),
    ]
    for thread in capture_threads:
        thread.start()

    print(
        f"Listening in {chunk_seconds}-second chunks. "
        f"Using a {OVERLAP_SECONDS}-second overlap per source. "
        f"Writing transcripts to {output_path.resolve()}",
        flush=True,
    )

    previous_chunks = {
        "Microphone": np.empty(0, dtype=np.float32),
        "System audio": np.empty(0, dtype=np.float32),
    }

    def transcribe_source(
        source_and_chunk: tuple[str, np.ndarray],
    ) -> tuple[str, str, np.ndarray]:
        source_name, chunk = source_and_chunk
        context = previous_chunks[source_name]
        audio = np.concatenate((context, chunk))
        text = transcribe_chunk(model, audio, language)
        return source_name, text, chunk

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            while not stop_event.is_set():
                chunks: list[tuple[str, np.ndarray]] = []
                try:
                    microphone_chunk = microphone_queue.get(timeout=0.25)
                    chunks.append(("Microphone", microphone_chunk))
                except queue.Empty:
                    pass
                try:
                    system_chunk = system_queue.get_nowait()
                    chunks.append(("System audio", system_chunk))
                except queue.Empty:
                    pass
                if not chunks:
                    continue
                results = executor.map(transcribe_source, chunks)
                for source_name, text, chunk in results:
                    if text:
                        append_transcript(output_path, source_name, text)
                        timestamp = datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        )
                        transcript_queue.put(
                            f"[{timestamp}] [{source_name}] {text}"
                        )
                    previous_chunks[source_name] = chunk[-overlap_frames:]
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
    finally:
        stop_event.set()
        transcript_queue.put(None)
        llm_thread.join(timeout=10)
        for thread in capture_threads:
            thread.join(timeout=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe microphone and Windows system audio live."
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List microphones and speakers, then exit.",
    )
    parser.add_argument("--mic", help="Microphone name or partial name.")
    parser.add_argument(
        "--speaker",
        help="Speaker name or partial name used for system-audio loopback.",
    )
    parser.add_argument(
        "--chunk-seconds",
        type=int,
        choices=(1, 2, 3, 4),
        default=DEFAULT_CHUNK_SECONDS,
        help="Audio chunk duration (1-4 seconds; default: 2).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Text file to append transcripts to (default: transcript.txt).",
    )
    parser.add_argument(
        "--response-output",
        type=Path,
        default=DEFAULT_RESPONSE_OUTPUT,
        help=(
            "Text file to append Gemini responses to "
            "(default: gemini_responses.txt)."
        ),
    )
    parser.add_argument(
        "--model",
        default="base",
        help="faster-whisper model size or local model path (default: base).",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        choices=("cpu", "cuda"),
        help="Inference device (default: cpu).",
    )
    parser.add_argument(
        "--compute-type",
        default=None,
        help="Override faster-whisper compute type, e.g. int8_float16.",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Language code such as en; omit for automatic detection.",
    )
    parser.add_argument(
        "--llm-model",
        default=DEFAULT_LLM_MODEL,
        help=f"Gemini model for suggestions (default: {DEFAULT_LLM_MODEL}).",
    )
    parser.add_argument(
        "--test-transcript",
        type=Path,
        help="Send a text file to Gemini instead of starting audio capture.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_devices:
        list_devices()
        return 0

    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to the environment before starting."
            )
        gemini_client = genai.Client(api_key=api_key)
        base_prompt = load_base_prompt()
        if args.test_transcript:
            run_transcript_test(
                args.test_transcript,
                gemini_client,
                args.llm_model,
                base_prompt,
            )
            return 0

        microphone = choose_microphone(args.mic)
        speaker = choose_speaker(args.speaker)
        compute_type = args.compute_type or (
            "int8" if args.device == "cpu" else "float16"
        )
        print(f"Loading faster-whisper model '{args.model}'...", flush=True)
        model = WhisperModel(
            args.model,
            device=args.device,
            compute_type=compute_type,
        )
        transcribe_live(
            model,
            microphone,
            speaker,
            args.output,
            args.chunk_seconds,
            args.language,
            gemini_client,
            args.llm_model,
            base_prompt,
            response_output_path=args.response_output,
        )
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())