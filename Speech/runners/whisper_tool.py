from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_OUTPUT_ROOT = Path("Output") / "Whisper"
SUPPORTED_FORMATS = ("json", "txt", "srt", "vtt")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_formats(value: str) -> list[str]:
    formats: list[str] = []
    for item in value.split(","):
        output_format = item.strip().lower()
        if not output_format:
            continue
        if output_format not in SUPPORTED_FORMATS:
            supported = ", ".join(SUPPORTED_FORMATS)
            raise ValueError(
                f"Unsupported output format: {output_format}. Supported formats: {supported}"
            )
        if output_format not in formats:
            formats.append(output_format)
    if not formats:
        raise ValueError("At least one output format is required")
    return formats


def build_output_paths(
    input_path: Path, output_dir: Path, formats: Iterable[str]
) -> dict[str, Path]:
    return {
        output_format: output_dir / f"{input_path.stem}.whisper.{output_format}"
        for output_format in formats
    }


def _sanitize_directory_name(value: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" .")
    if not sanitized:
        sanitized = "transcription"
    if sanitized.upper() in WINDOWS_RESERVED_NAMES:
        sanitized += "_"
    return sanitized[:100].rstrip(" .")


def choose_output_dir(
    output_dir: Path | None,
    input_path: Path,
    job_name: str | None,
    now: datetime | None = None,
    *,
    fallback_root: Path = DEFAULT_OUTPUT_ROOT,
) -> Path:
    if output_dir is not None:
        return output_dir.resolve()

    local_now = now or datetime.now().astimezone()
    purpose = _sanitize_directory_name(job_name or input_path.stem)
    base_name = f"{local_now:%Y-%m-%d} {purpose}"
    candidate = fallback_root / base_name
    if not candidate.exists():
        return candidate

    timed_name = f"{base_name} {local_now:%H-%M-%S}"
    candidate = fallback_root / timed_name
    suffix = 2
    while candidate.exists():
        candidate = fallback_root / f"{timed_name} {suffix}"
        suffix += 1
    return candidate


def format_timestamp(seconds: float, decimal_mark: str) -> str:
    total_milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return (
        f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"
        f"{decimal_mark}{milliseconds:03d}"
    )


def render_srt(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            "\n".join(
                (
                    str(index),
                    f"{format_timestamp(segment['start'], ',')} --> "
                    f"{format_timestamp(segment['end'], ',')}",
                    segment["text"].strip(),
                )
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_vtt(segments: list[dict[str, Any]]) -> str:
    blocks = ["WEBVTT"]
    for segment in segments:
        blocks.append(
            "\n".join(
                (
                    f"{format_timestamp(segment['start'], '.')} --> "
                    f"{format_timestamp(segment['end'], '.')}",
                    segment["text"].strip(),
                )
            )
        )
    return "\n\n".join(blocks) + "\n"


def _word_to_dict(word: Any) -> dict[str, Any]:
    return {
        "start": word.start,
        "end": word.end,
        "word": word.word,
        "probability": word.probability,
    }


def _segment_to_dict(segment: Any) -> dict[str, Any]:
    result = {
        "id": segment.id,
        "seek": segment.seek,
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
        "tokens": list(segment.tokens),
        "temperature": segment.temperature,
        "avg_logprob": segment.avg_logprob,
        "compression_ratio": segment.compression_ratio,
        "no_speech_prob": segment.no_speech_prob,
    }
    if segment.words is not None:
        result["words"] = [_word_to_dict(word) for word in segment.words]
    return result


def _read_initial_prompt(args: argparse.Namespace) -> str | None:
    if args.initial_prompt_file:
        return args.initial_prompt_file.read_text(encoding="utf-8").strip() or None
    return args.initial_prompt


def _write_outputs(
    paths: dict[str, Path], metadata: dict[str, Any], segments: list[dict[str, Any]]
) -> None:
    contents: dict[Path, str] = {}
    for output_format, path in paths.items():
        if output_format == "json":
            content = json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
        elif output_format == "txt":
            content = "\n".join(segment["text"].strip() for segment in segments) + "\n"
        elif output_format == "srt":
            content = render_srt(segments)
        else:
            content = render_vtt(segments)
        contents[path] = content
    _publish_new_outputs(contents)


def _publish_new_outputs(contents: dict[Path, str]) -> None:
    occupied = [path for path in contents if path.exists() or path.is_symlink()]
    if occupied:
        raise FileExistsError(f"Output already exists; choose new output paths: {occupied[0]}")
    created: list[Path] = []
    try:
        with contextlib.ExitStack() as stack:
            files = []
            for path, content in contents.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                output = stack.enter_context(path.open("x", encoding="utf-8"))
                created.append(path)
                files.append((output, content))
            for output, content in files:
                output.write(content)
    except Exception:
        leftovers = []
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                leftovers.append(str(path))
        if leftovers:
            raise RuntimeError(f"Incomplete exports remain at: {', '.join(leftovers)}")
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe or translate local audio and video with faster-whisper."
    )
    parser.add_argument("--input", required=True, type=Path, help="Local audio or video file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for generated files; omit to use the shared fallback output",
    )
    parser.add_argument(
        "--job-name",
        help="Short purpose used only to name a fallback output directory",
    )
    parser.add_argument(
        "--model", required=True, help="Local CTranslate2 model directory"
    )
    parser.add_argument("--language", help="ISO language code; omit for auto-detection")
    parser.add_argument(
        "--task", choices=("transcribe", "translate"), default="transcribe"
    )
    parser.add_argument(
        "--formats",
        default="json",
        help="Comma-separated output formats: json, txt, srt, vtt",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Print run metadata without transcript text; default prints output paths only",
    )
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--initial-prompt", help="Short recognition context")
    prompt_group.add_argument(
        "--initial-prompt-file", type=Path, help="UTF-8 file with short recognition context"
    )
    parser.add_argument("--device", choices=("cuda", "cpu", "auto"), default="cuda")
    parser.add_argument("--compute-type", default="int8_float16")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument(
        "--vad-filter", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument(
        "--word-timestamps", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument(
        "--condition-on-previous-text",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    input_path = args.input.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if args.initial_prompt_file and not args.initial_prompt_file.is_file():
        raise FileNotFoundError(
            f"Initial prompt file not found: {args.initial_prompt_file}"
        )

    formats = parse_formats(args.formats)
    source_sha256 = sha256_file(input_path)
    output_dir = choose_output_dir(
        args.output_dir,
        input_path,
        args.job_name,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = build_output_paths(input_path, output_dir, formats)
    if any(path.exists() or path.is_symlink() for path in output_paths.values()):
        raise FileExistsError("Output already exists; choose a new output directory")

    from faster_whisper import WhisperModel

    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
    )
    initial_prompt = _read_initial_prompt(args)
    segment_iterator, info = model.transcribe(
        str(input_path),
        language=args.language,
        task=args.task,
        beam_size=args.beam_size,
        vad_filter=args.vad_filter,
        word_timestamps=args.word_timestamps,
        condition_on_previous_text=args.condition_on_previous_text,
        initial_prompt=initial_prompt,
    )
    segments = [_segment_to_dict(segment) for segment in segment_iterator]
    if sha256_file(input_path) != source_sha256:
        raise ValueError("Source file changed during recognition; outputs were not published")

    metadata = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(input_path),
        "source_sha256": source_sha256,
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "task": args.task,
        "recognition_options": {
            "beam_size": args.beam_size,
            "vad_filter": args.vad_filter,
            "word_timestamps": args.word_timestamps,
            "condition_on_previous_text": args.condition_on_previous_text,
            "initial_prompt_sha256": (
                hashlib.sha256(initial_prompt.encode("utf-8")).hexdigest()
                if initial_prompt is not None else None
            ),
        },
        "requested_language": args.language,
        "detected_language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "duration_after_vad": info.duration_after_vad,
        "segments": segments,
    }
    _write_outputs(output_paths, metadata, segments)
    metadata["outputs"] = {
        output_format: str(path) for output_format, path in output_paths.items()
    }
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        metadata = run(args)
    except Exception as error:
        print(f"Whisper failed: {error}", file=sys.stderr)
        return 1
    report = {key: value for key, value in metadata.items() if key != "segments"}
    print(json.dumps(report if args.report else metadata["outputs"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
