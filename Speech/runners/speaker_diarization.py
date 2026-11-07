from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_DLL_HANDLES: list[Any] = []


def find_ffmpeg_dll_dir(
    path_value: str | None = None,
    packages_root: Path | None = None,
) -> Path | None:
    for value in (path_value if path_value is not None else os.environ.get("PATH", "")).split(
        os.pathsep
    ):
        candidate = Path(value.strip('"')) if value else None
        if candidate and candidate.is_dir() and next(candidate.glob("avcodec-*.dll"), None):
            return candidate

    root = packages_root
    if root is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return None
        root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if not root.is_dir():
        return None
    for candidate in root.glob("BtbN.FFmpeg.GPL.Shared.8.1_*/*/bin"):
        if next(candidate.glob("avcodec-*.dll"), None):
            return candidate
    return None


def configure_ffmpeg() -> Path:
    directory = find_ffmpeg_dll_dir()
    if directory is None:
        raise RuntimeError("Compatible shared FFmpeg DLL directory was not found")
    if hasattr(os, "add_dll_directory"):
        _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
    return directory


def validate_speaker_bounds(
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
) -> tuple[int | None, int | None, int | None]:
    values = (num_speakers, min_speakers, max_speakers)
    if any(value is not None and value < 1 for value in values):
        raise ValueError("Speaker counts must be positive integers")
    if num_speakers is not None and (min_speakers is not None or max_speakers is not None):
        raise ValueError("--num-speakers cannot be combined with speaker range options")
    if min_speakers is not None and max_speakers is not None and min_speakers > max_speakers:
        raise ValueError("Speaker minimum cannot exceed maximum")
    return num_speakers, min_speakers, max_speakers


def annotation_to_segments(annotation: Any) -> list[dict[str, Any]]:
    return [
        {
            "start": float(turn.start),
            "end": float(turn.end),
            "speaker": str(speaker),
        }
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]


def get_annotations(output: Any) -> tuple[Any, Any]:
    regular = output.speaker_diarization
    exclusive = getattr(output, "exclusive_speaker_diarization", None) or regular
    return regular, exclusive


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split local audio or video into anonymous speaker time intervals."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-rttm", type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--num-speakers", type=int)
    parser.add_argument("--min-speakers", type=int)
    parser.add_argument("--max-speakers", type=int)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    input_path = args.input.resolve()
    output_json = args.output_json.resolve()
    output_rttm = args.output_rttm.resolve() if args.output_rttm else None
    model_path = args.model.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if not model_path.is_dir():
        raise FileNotFoundError(f"Model directory not found: {model_path}")
    if input_path in {output_json, output_rttm}:
        raise ValueError("An output path cannot overwrite the input file")
    num_speakers, min_speakers, max_speakers = validate_speaker_bounds(
        args.num_speakers, args.min_speakers, args.max_speakers
    )

    ffmpeg_directory = configure_ffmpeg()
    import torch
    from pyannote.audio import Pipeline

    if args.device == "auto":
        device_name = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device_name = args.device
    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")

    pipeline = Pipeline.from_pretrained(str(model_path))
    pipeline.to(torch.device(device_name))
    options = {
        key: value
        for key, value in {
            "num_speakers": num_speakers,
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
        }.items()
        if value is not None
    }
    output = pipeline(str(input_path), **options)
    annotation, exclusive_annotation = get_annotations(output)
    segments = annotation_to_segments(annotation)
    exclusive_segments = annotation_to_segments(exclusive_annotation)
    speakers = sorted({segment["speaker"] for segment in segments})

    if output_rttm:
        # RTTM separates columns with whitespace; keep the source path unchanged in JSON.
        annotation.uri = "_".join((annotation.uri or input_path.stem).split()) or "recording"
        output_rttm.parent.mkdir(parents=True, exist_ok=True)
        with output_rttm.open("w", encoding="utf-8") as destination:
            annotation.write_rttm(destination)

    result = {
        "schema_version": 1,
        "type": "speaker-diarization",
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(input_path),
        "source_sha256": sha256_file(input_path),
        "model": str(model_path),
        "device": device_name,
        "ffmpeg_dll_directory": str(ffmpeg_directory),
        "parameters": options,
        "speakers": speakers,
        "segments": segments,
        "exclusive_segments": exclusive_segments,
        "outputs": {
            "json": str(output_json),
            **({"rttm": str(output_rttm)} if output_rttm else {}),
        },
    }
    write_json(output_json, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        result = run(parser.parse_args(argv))
    except Exception as error:
        print(f"Speaker diarization failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result["outputs"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
