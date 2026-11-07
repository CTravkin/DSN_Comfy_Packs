from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence


SAMPLE_RATE = 16_000
MINIMUM_DURATION_SECONDS = 0.5
_DLL_HANDLES: list[Any] = []


def find_ffmpeg_dll_dir() -> Path | None:
    for value in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(value.strip('"')) if value else None
        if candidate and candidate.is_dir() and next(candidate.glob("avcodec-*.dll"), None):
            return candidate
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    for candidate in packages.glob("BtbN.FFmpeg.GPL.Shared.8.1_*/*/bin"):
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


def validate_time_range(start: float, end: float | None) -> tuple[float, float | None]:
    if start < 0:
        raise ValueError("Start time cannot be negative")
    if end is not None and end <= start:
        raise ValueError("End time must be greater than start time")
    return start, end


def slice_samples(
    samples: Sequence[Any],
    *,
    sample_rate: int,
    start: float,
    end: float | None,
    minimum_samples: int,
) -> tuple[Any, float, float]:
    validate_time_range(start, end)
    first = round(start * sample_rate)
    last = len(samples) if end is None else min(len(samples), round(end * sample_rate))
    if first >= len(samples):
        raise ValueError("Start time is outside the decoded audio")
    chunk = samples[first:last]
    if len(chunk) < minimum_samples:
        raise ValueError("Selected audio is too short for a voiceprint")
    return chunk, first / sample_rate, last / sample_rate


def normalize_vector(values: Sequence[float]) -> list[float]:
    vector = [float(value) for value in values]
    magnitude = math.sqrt(sum(value * value for value in vector))
    if not vector or not math.isfinite(magnitude) or magnitude == 0:
        raise ValueError("Embedding cannot be normalized")
    return [value / magnitude for value in vector]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Voiceprint dimensions do not match")
    normalized_left = normalize_vector(left)
    normalized_right = normalize_vector(right)
    return sum(a * b for a, b in zip(normalized_left, normalized_right, strict=True))


def average_vectors(vectors: Sequence[Sequence[float]]) -> list[float]:
    if not vectors:
        raise ValueError("At least one embedding is required")
    dimensions = len(vectors[0])
    if dimensions == 0 or any(len(vector) != dimensions for vector in vectors):
        raise ValueError("Voiceprint dimensions do not match")
    centroid = [
        sum(float(vector[index]) for vector in vectors) / len(vectors)
        for index in range(dimensions)
    ]
    return normalize_vector(centroid)


def group_segments(
    diarization: dict[str, Any],
    *,
    minimum_duration: float,
    max_segments: int,
) -> dict[str, list[tuple[float, float]]]:
    timeline = diarization.get("exclusive_segments") or diarization.get("segments") or []
    grouped: dict[str, list[tuple[float, float]]] = {}
    for segment in timeline:
        start = float(segment["start"])
        end = float(segment["end"])
        speaker = str(segment["speaker"])
        grouped.setdefault(speaker, [])
        if end - start >= minimum_duration:
            grouped[speaker].append((start, end))
    return {
        speaker: sorted(intervals, key=lambda item: (item[1] - item[0], -item[0]), reverse=True)[
            :max_segments
        ]
        for speaker, intervals in grouped.items()
    }


def validate_reference_model(
    document: dict[str, Any], expected_model: dict[str, Any], path: Path
) -> None:
    reference_model = document.get("model")
    if not isinstance(reference_model, dict):
        raise ValueError(f"Voiceprint has no model metadata: {path}")
    compared = False
    for field in ("source", "revision", "architecture"):
        expected = expected_model.get(field)
        if expected is None:
            continue
        compared = True
        if reference_model.get(field) != expected:
            label = field.replace("_", " ")
            raise ValueError(f"Voiceprint model {label} does not match: {path}")
    if not compared:
        expected_path = expected_model.get("path")
        reference_path = reference_model.get("path")
        if not expected_path or not reference_path or os.path.normcase(
            os.path.abspath(reference_path)
        ) != os.path.normcase(os.path.abspath(expected_path)):
            raise ValueError(f"Voiceprint model path does not match: {path}")


def load_reference_registry(
    root: Path, *, expected_model: dict[str, Any]
) -> dict[str, list[list[float]]]:
    if not root.is_dir():
        raise FileNotFoundError(f"Reference directory not found: {root}")
    registry: dict[str, list[list[float]]] = {}
    for path in root.rglob("*.json"):
        relative = path.relative_to(root)
        if len(relative.parts) < 2:
            continue
        document = load_voiceprint(path)
        validate_reference_model(document, expected_model, path)
        dimensions = document.get("embedding_dimensions")
        if dimensions != len(document["embedding"]):
            raise ValueError(f"Voiceprint embedding dimensions do not match metadata: {path}")
        registry.setdefault(relative.parts[0], []).append(
            normalize_vector(document["embedding"])
        )
    if not registry:
        raise ValueError(f"No voiceprint references found under: {root}")
    return registry


def rank_identities(
    query: Sequence[float], registry: dict[str, list[list[float]]]
) -> list[dict[str, Any]]:
    ranking = [
        {
            "identity": identity,
            "score": cosine_similarity(query, average_vectors(references)),
            "reference_count": len(references),
        }
        for identity, references in registry.items()
    ]
    return sorted(ranking, key=lambda item: (-item["score"], item["identity"]))


def decide_identity(
    ranking: list[dict[str, Any]], *, threshold: float, minimum_margin: float
) -> dict[str, Any]:
    if not ranking:
        return {
            "status": "unknown",
            "identity": None,
            "score": None,
            "runner_up": None,
            "margin": None,
        }
    best = ranking[0]
    second = ranking[1] if len(ranking) > 1 else None
    margin = best["score"] - second["score"] if second else None
    identified = best["score"] >= threshold and (
        margin is None or margin >= minimum_margin
    )
    return {
        "status": "identified" if identified else "unknown",
        "identity": best["identity"] if identified else None,
        "score": best["score"],
        "runner_up": second["identity"] if second else None,
        "margin": margin,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_distinct_paths(paths: dict[str, Path]) -> None:
    seen: dict[str, str] = {}
    for label, path in paths.items():
        normalized = os.path.normcase(str(path.resolve()))
        if normalized in seen:
            raise ValueError(f"{label} and {seen[normalized]} paths must be different")
        seen[normalized] = label


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def load_voiceprint(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("type") != "voiceprint" or not isinstance(document.get("embedding"), list):
        raise ValueError(f"Not a voiceprint document: {path}")
    if not document["embedding"]:
        raise ValueError(f"Voiceprint embedding is empty: {path}")
    return document


def model_metadata(model_path: Path) -> dict[str, Any]:
    manifest_path = model_path / "manifest.json"
    if not manifest_path.is_file():
        return {"path": str(model_path)}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "path": str(model_path),
        "source": manifest.get("source"),
        "revision": manifest.get("revision"),
        "architecture": manifest.get("architecture"),
    }


def extract(args: argparse.Namespace) -> dict[str, Any]:
    input_path = args.input.resolve()
    output_path = args.output_json.resolve()
    model_path = args.model.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if not model_path.is_dir():
        raise FileNotFoundError(f"Model directory not found: {model_path}")
    if input_path == output_path:
        raise ValueError("Output path cannot overwrite the input file")
    validate_time_range(args.start, args.end)

    ffmpeg_directory = configure_ffmpeg()
    import torch
    from torchcodec.decoders import AudioDecoder
    from speechbrain.inference.speaker import EncoderClassifier

    if args.device == "auto":
        device_name = "cuda:0" if torch.cuda.is_available() else "cpu"
    else:
        device_name = "cuda:0" if args.device == "cuda" else "cpu"
    if device_name.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")

    decoded = AudioDecoder(input_path, sample_rate=SAMPLE_RATE, num_channels=1).get_all_samples()
    samples, actual_start, actual_end = slice_samples(
        decoded.data[0],
        sample_rate=SAMPLE_RATE,
        start=args.start,
        end=args.end,
        minimum_samples=round(MINIMUM_DURATION_SECONDS * SAMPLE_RATE),
    )
    classifier = EncoderClassifier.from_hparams(
        source=str(model_path),
        savedir=str(args.cache_dir.resolve()),
        run_opts={"device": device_name},
    )
    waveform = samples.unsqueeze(0).to(device_name)
    embedding = classifier.encode_batch(waveform).detach().cpu().reshape(-1).tolist()
    vector = normalize_vector(embedding)
    model = model_metadata(model_path)
    result = {
        "schema_version": 1,
        "type": "voiceprint",
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(input_path),
        "source_sha256": sha256_file(input_path),
        "time_range": {"start": actual_start, "end": actual_end},
        "sample_rate": SAMPLE_RATE,
        "model": model,
        "device": device_name,
        "ffmpeg_dll_directory": str(ffmpeg_directory),
        "embedding_dimensions": len(vector),
        "embedding": vector,
        "outputs": {"json": str(output_path)},
    }
    write_json(output_path, result)
    return result


def compare(args: argparse.Namespace) -> dict[str, Any]:
    left_path = args.left.resolve()
    right_path = args.right.resolve()
    output_path = args.output_json.resolve()
    validate_distinct_paths(
        {
            "left voiceprint": left_path,
            "right voiceprint": right_path,
            "output JSON": output_path,
        }
    )
    left = load_voiceprint(left_path)
    right = load_voiceprint(right_path)
    left_model = left.get("model")
    if not isinstance(left_model, dict):
        raise ValueError(f"Voiceprint has no model metadata: {left_path}")
    validate_reference_model(right, left_model, right_path)
    for document, path in ((left, left_path), (right, right_path)):
        if document.get("embedding_dimensions") != len(document["embedding"]):
            raise ValueError(f"Voiceprint embedding dimensions do not match metadata: {path}")
    similarity = cosine_similarity(left["embedding"], right["embedding"])
    result = {
        "schema_version": 1,
        "type": "voiceprint-comparison",
        "created_at": datetime.now(UTC).isoformat(),
        "left": str(left_path),
        "right": str(right_path),
        "model_revision": left_model.get("revision"),
        "embedding_dimensions": len(left["embedding"]),
        "cosine_similarity": similarity,
        "outputs": {"json": str(output_path)},
    }
    write_json(output_path, result)
    return result


def identify(args: argparse.Namespace) -> dict[str, Any]:
    input_path = args.input.resolve()
    diarization_path = args.diarization_json.resolve()
    reference_root = args.reference_root.resolve()
    output_path = args.output_json.resolve()
    model_path = args.model.resolve()
    validate_distinct_paths(
        {
            "input recording": input_path,
            "diarization JSON": diarization_path,
            "output JSON": output_path,
        }
    )
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if not diarization_path.is_file():
        raise FileNotFoundError(f"Diarization JSON not found: {diarization_path}")
    if not model_path.is_dir():
        raise FileNotFoundError(f"Model directory not found: {model_path}")
    if not -1.0 <= args.threshold <= 1.0:
        raise ValueError("--threshold must be between -1 and 1")
    if args.minimum_margin < 0:
        raise ValueError("--minimum-margin cannot be negative")
    if args.minimum_duration < MINIMUM_DURATION_SECONDS:
        raise ValueError(
            f"--minimum-duration cannot be below {MINIMUM_DURATION_SECONDS} seconds"
        )
    if args.max_segments < 1:
        raise ValueError("--max-segments must be positive")

    diarization_sha256 = sha256_file(diarization_path)
    diarization = json.loads(diarization_path.read_text(encoding="utf-8"))
    if diarization.get("type") != "speaker-diarization":
        raise ValueError("Diarization JSON is not a speaker-diarization document")
    source = diarization.get("source")
    if not source or os.path.normcase(os.path.abspath(source)) != os.path.normcase(
        str(input_path)
    ):
        raise ValueError("Diarization refers to a different source file")
    source_hash = sha256_file(input_path)
    diarization_hash = diarization.get("source_sha256")
    if not diarization_hash or diarization_hash != source_hash:
        raise ValueError("Diarization source hash does not match the input file")
    segments = group_segments(
        diarization,
        minimum_duration=args.minimum_duration,
        max_segments=args.max_segments,
    )
    if not segments:
        raise ValueError("Diarization contains no usable speaker intervals")
    current_model = model_metadata(model_path)
    registry = load_reference_registry(reference_root, expected_model=current_model)

    ffmpeg_directory = configure_ffmpeg()
    import torch
    from torchcodec.decoders import AudioDecoder
    from speechbrain.inference.speaker import EncoderClassifier

    if args.device == "auto":
        device_name = "cuda:0" if torch.cuda.is_available() else "cpu"
    else:
        device_name = "cuda:0" if args.device == "cuda" else "cpu"
    if device_name.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")

    decoded = AudioDecoder(input_path, sample_rate=SAMPLE_RATE, num_channels=1).get_all_samples()
    classifier = EncoderClassifier.from_hparams(
        source=str(model_path),
        savedir=str(args.cache_dir.resolve()),
        run_opts={"device": device_name},
    )
    results: dict[str, Any] = {}
    mapping: dict[str, str] = {}
    for speaker, intervals in sorted(segments.items()):
        if not intervals:
            results[speaker] = {
                **decide_identity(
                    [],
                    threshold=args.threshold,
                    minimum_margin=args.minimum_margin,
                ),
                "reason": "no_usable_segments",
                "segments_used": [],
                "ranking": [],
            }
            continue
        vectors = []
        used = []
        for start, end in intervals:
            samples, actual_start, actual_end = slice_samples(
                decoded.data[0],
                sample_rate=SAMPLE_RATE,
                start=start,
                end=end,
                minimum_samples=round(MINIMUM_DURATION_SECONDS * SAMPLE_RATE),
            )
            waveform = samples.unsqueeze(0).to(device_name)
            embedding = (
                classifier.encode_batch(waveform).detach().cpu().reshape(-1).tolist()
            )
            vectors.append(normalize_vector(embedding))
            used.append({"start": actual_start, "end": actual_end})
        ranking = rank_identities(average_vectors(vectors), registry)
        decision = decide_identity(
            ranking,
            threshold=args.threshold,
            minimum_margin=args.minimum_margin,
        )
        results[speaker] = {**decision, "segments_used": used, "ranking": ranking}
        if decision["identity"] is not None:
            mapping[speaker] = decision["identity"]

    result = {
        "schema_version": 1,
        "type": "speaker-identification",
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(input_path),
        "source_sha256": source_hash,
        "diarization_json": str(diarization_path),
        "diarization_sha256": diarization_sha256,
        "reference_root": str(reference_root),
        "model": current_model,
        "device": device_name,
        "ffmpeg_dll_directory": str(ffmpeg_directory),
        "parameters": {
            "threshold": args.threshold,
            "minimum_margin": args.minimum_margin,
            "minimum_duration": args.minimum_duration,
            "max_segments": args.max_segments,
        },
        "speakers": results,
        "mapping": mapping,
        "outputs": {"json": str(output_path)},
    }
    write_json(output_path, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract and compare local ECAPA voice embeddings."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("--input", required=True, type=Path)
    extract_parser.add_argument("--output-json", required=True, type=Path)
    extract_parser.add_argument("--start", type=float, default=0.0)
    extract_parser.add_argument("--end", type=float)
    extract_parser.add_argument("--model", required=True, type=Path)
    extract_parser.add_argument("--cache-dir", required=True, type=Path)
    extract_parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    extract_parser.set_defaults(handler=extract)

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--left", required=True, type=Path)
    compare_parser.add_argument("--right", required=True, type=Path)
    compare_parser.add_argument("--output-json", required=True, type=Path)
    compare_parser.set_defaults(handler=compare)

    identify_parser = subparsers.add_parser("identify")
    identify_parser.add_argument("--input", required=True, type=Path)
    identify_parser.add_argument("--diarization-json", required=True, type=Path)
    identify_parser.add_argument("--reference-root", required=True, type=Path)
    identify_parser.add_argument("--output-json", required=True, type=Path)
    identify_parser.add_argument("--threshold", type=float, default=0.25)
    identify_parser.add_argument("--minimum-margin", type=float, default=0.05)
    identify_parser.add_argument("--minimum-duration", type=float, default=1.0)
    identify_parser.add_argument("--max-segments", type=int, default=5)
    identify_parser.add_argument("--model", required=True, type=Path)
    identify_parser.add_argument("--cache-dir", required=True, type=Path)
    identify_parser.add_argument(
        "--device", choices=("auto", "cuda", "cpu"), default="auto"
    )
    identify_parser.set_defaults(handler=identify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        result = args.handler(args)
    except Exception as error:
        print(f"Voiceprint failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result["outputs"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
