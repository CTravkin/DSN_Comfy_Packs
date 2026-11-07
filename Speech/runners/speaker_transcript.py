from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


UNKNOWN_SPEAKER = "UNKNOWN"


def overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def choose_speaker(start: float, end: float, segments: list[dict[str, Any]]) -> str:
    matches = [
        (overlap(start, end, float(item["start"]), float(item["end"])), str(item["speaker"]))
        for item in segments
    ]
    positive = [match for match in matches if match[0] > 0]
    if not positive:
        return UNKNOWN_SPEAKER
    return max(positive, key=lambda match: (match[0], match[1]))[1]


def source_fingerprint(document: dict[str, Any], name: str) -> str:
    value = document.get("source_sha256")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError(
            f"{name} must contain a valid source_sha256; regenerate unverified legacy output"
        )
    return value.lower()


def validate_sources(transcript: dict[str, Any], diarization: dict[str, Any]) -> None:
    left = transcript.get("source")
    right = diarization.get("source")
    if not left or not right:
        raise ValueError("Both inputs must contain a source path")
    if os.path.normcase(os.path.abspath(left)) != os.path.normcase(os.path.abspath(right)):
        raise ValueError("Transcript and diarization refer to different source files")
    if source_fingerprint(transcript, "Transcript") != source_fingerprint(diarization, "Diarization"):
        raise ValueError("Transcript and diarization source hashes do not match")


def _validate_timeline(name: str, segments: Any) -> None:
    if not isinstance(segments, list):
        raise ValueError(f"{name} must be a list")
    for segment in segments:
        if not isinstance(segment, dict) or not all(
            key in segment for key in ("start", "end", "speaker")
        ):
            raise ValueError(f"{name} contains an invalid speaker interval")
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} contains an invalid speaker interval") from error
        speaker = str(segment["speaker"]).strip()
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or not speaker:
            raise ValueError(f"{name} contains an invalid speaker interval")


def validate_documents(
    transcript: dict[str, Any], diarization: dict[str, Any]
) -> None:
    transcript_type = transcript.get("type")
    if transcript_type not in (None, "whisper-transcript"):
        raise ValueError("Transcript JSON is not a whisper-transcript document")
    if not isinstance(transcript.get("segments"), list):
        raise ValueError("Transcript JSON must contain a segments list")
    if diarization.get("type") != "speaker-diarization":
        raise ValueError("Diarization JSON is not a speaker-diarization document")
    _validate_timeline("Diarization segments", diarization.get("segments"))
    if "exclusive_segments" in diarization:
        _validate_timeline(
            "Diarization exclusive_segments", diarization["exclusive_segments"]
        )
    validate_sources(transcript, diarization)


def validate_mapping_document(
    document: dict[str, Any],
    transcript: dict[str, Any],
    diarization: dict[str, Any],
    diarization_path: Path,
    diarization_sha256: str,
) -> dict[str, str]:
    if document.get("type") != "speaker-identification":
        raise ValueError("Speaker map is not a speaker-identification document")
    map_source = document.get("source")
    transcript_source = transcript.get("source")
    if not map_source or os.path.normcase(os.path.abspath(map_source)) != os.path.normcase(
        os.path.abspath(transcript_source)
    ):
        raise ValueError("Speaker map refers to a different source file")
    if source_fingerprint(document, "Speaker map") != source_fingerprint(diarization, "Diarization"):
        raise ValueError("Speaker map source hash does not match diarization")
    mapped_diarization = document.get("diarization_json")
    if not mapped_diarization or os.path.normcase(
        os.path.abspath(mapped_diarization)
    ) != os.path.normcase(str(diarization_path)):
        raise ValueError("Speaker map refers to a different diarization JSON")
    if document.get("diarization_sha256") != diarization_sha256:
        raise ValueError("Speaker map diarization hash does not match")
    raw_mapping = document.get("mapping")
    if not isinstance(raw_mapping, dict):
        raise ValueError("Speaker map must contain a mapping object")
    known_speakers = {
        str(item["speaker"])
        for item in (
            diarization.get("exclusive_segments") or diarization.get("segments") or []
        )
    }
    mapping = {str(key): str(value) for key, value in raw_mapping.items()}
    if any(not value.strip() for value in mapping.values()):
        raise ValueError("Speaker map identities cannot be empty")
    unknown_labels = sorted(set(mapping) - known_speakers)
    if unknown_labels:
        raise ValueError(
            "Speaker map contains labels absent from diarization: "
            + ", ".join(unknown_labels)
        )
    return mapping


def validate_distinct_paths(paths: dict[str, Path]) -> None:
    seen: dict[str, str] = {}
    for label, path in paths.items():
        normalized = os.path.normcase(str(path.resolve()))
        if normalized in seen:
            raise ValueError(f"{label} and {seen[normalized]} paths must be different")
        seen[normalized] = label


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def join_word_text(words: list[dict[str, Any]]) -> str:
    text = ""
    for word in words:
        token = str(word.get("word", ""))
        if text and token and not token[0].isspace() and token[0] not in '.,!?;:%)]}"':
            text += " "
        text += token
    return text.strip()


def _word_items(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        word
        for segment in transcript.get("segments", [])
        for word in segment.get("words", []) or []
        if word.get("start") is not None and word.get("end") is not None
    ]


def build_turns(
    transcript: dict[str, Any],
    diarization: dict[str, Any],
    mapping: dict[str, str],
    *,
    max_gap: float,
    mapping_confirmed: bool = False,
) -> list[dict[str, Any]]:
    intervals = diarization.get("exclusive_segments") or diarization.get("segments") or []
    items = []
    for segment in transcript.get("segments", []):
        words = _word_items({"segments": [segment]})
        supplied_words = segment.get("words") or []
        if words and len(words) == len(supplied_words):
            segment_text = str(segment.get("text", ""))
            compact = lambda value: "".join(char for char in value.casefold().replace("ё", "е") if char.isalnum())
            if segment_text.strip() and compact(segment_text) != compact(join_word_text(words)):
                raise ValueError("Segment text disagrees with timed words; verify recognition input")
            items.extend([
            {
                "start": float(word["start"]),
                "end": float(word["end"]),
                "word": word,
                "anonymous_speaker": choose_speaker(
                    float(word["start"]), float(word["end"]), intervals
                ),
            }
                for word in words
            ])
        else:
            text = str(segment.get("text", "")).strip() or join_word_text(supplied_words)
            if not text:
                continue
            items.append(
            {
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "text": text,
                "anonymous_speaker": choose_speaker(
                    float(segment["start"]), float(segment["end"]), intervals
                ),
            })

    groups: list[list[dict[str, Any]]] = []
    for item in items:
        if (
            groups
            and groups[-1][-1]["anonymous_speaker"] == item["anonymous_speaker"]
            and item["start"] - groups[-1][-1]["end"] <= max_gap
        ):
            groups[-1].append(item)
        else:
            groups.append([item])

    turns = []
    for group in groups:
        anonymous = group[0]["anonymous_speaker"]
        group_words = [item["word"] for item in group if "word" in item]
        fallbacks = [{"start": item["start"], "end": item["end"], "text": item["text"]} for item in group if "text" in item]
        text = join_word_text([
            item["word"] if "word" in item else {"word": item["text"]} for item in group
        ])
        candidate = mapping.get(anonymous)
        turn = {
            "start": group[0]["start"],
            "end": group[-1]["end"],
            "speaker": candidate if candidate and mapping_confirmed else anonymous,
            "anonymous_speaker": anonymous,
            "text": text,
            "identity_status": "confirmed" if candidate and mapping_confirmed else "candidate" if candidate else "anonymous",
            "alignment_granularity": "mixed" if group_words and fallbacks else "word" if group_words else "segment",
        }
        if group_words:
            turn["words"] = group_words
        if fallbacks:
            turn["segment_fallbacks"] = fallbacks
        if candidate and not mapping_confirmed:
            turn["candidate_speaker"] = candidate
        turns.append(turn)
    return turns


def format_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def render_txt(turns: list[dict[str, Any]]) -> str:
    lines = []
    for turn in turns:
        label = turn["speaker"]
        if turn.get("identity_status") == "candidate":
            label += f" [candidate: {turn['candidate_speaker']}]"
        lines.append(f"[{format_timestamp(turn['start'])} - {format_timestamp(turn['end'])}] {label}: {turn['text']}\n")
    return "".join(lines)


def write_text(path: Path, content: str) -> None:
    write_texts({path: content})


def write_texts(contents: dict[Path, str]) -> None:
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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_with_hash(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_bytes()
    return json.loads(content), hashlib.sha256(content).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Align timestamped transcript text with speaker intervals."
    )
    parser.add_argument("--transcript-json", required=True, type=Path)
    parser.add_argument("--diarization-json", required=True, type=Path)
    parser.add_argument("--speaker-map-json", type=Path)
    parser.add_argument("--speaker-map-confirmed", action="store_true", help="Caller confirms every identity in the supplied map")
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-txt", type=Path)
    parser.add_argument("--max-gap", type=float, default=1.0)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    transcript_path = args.transcript_json.resolve()
    diarization_path = args.diarization_json.resolve()
    output_json = args.output_json.resolve()
    output_txt = args.output_txt.resolve() if args.output_txt else None
    map_path = args.speaker_map_json.resolve() if args.speaker_map_json else None
    validate_distinct_paths(
        {
            "transcript JSON": transcript_path,
            "diarization JSON": diarization_path,
            **({"speaker map JSON": map_path} if map_path else {}),
            "output JSON": output_json,
            **({"output TXT": output_txt} if output_txt else {}),
        }
    )
    if args.speaker_map_confirmed and not map_path:
        raise ValueError("--speaker-map-confirmed requires --speaker-map-json")
    if any(path and (path.exists() or path.is_symlink()) for path in (output_json, output_txt)):
        raise FileExistsError("Output already exists; choose new output paths")
    for path in (transcript_path, diarization_path):
        if not path.is_file():
            raise FileNotFoundError(f"Input JSON not found: {path}")
    if args.max_gap < 0:
        raise ValueError("--max-gap cannot be negative")

    transcript, transcript_sha256 = read_json_with_hash(transcript_path)
    diarization, diarization_sha256 = read_json_with_hash(diarization_path)
    fingerprints = {transcript_path: transcript_sha256, diarization_path: diarization_sha256}
    validate_documents(transcript, diarization)
    mapping: dict[str, str] = {}
    map_sha256 = None
    if map_path:
        mapping_document, map_sha256 = read_json_with_hash(map_path)
        fingerprints[map_path] = map_sha256
        mapping = validate_mapping_document(
            mapping_document,
            transcript,
            diarization,
            diarization_path,
            diarization_sha256,
        )

    turns = build_turns(transcript, diarization, mapping, max_gap=args.max_gap, mapping_confirmed=args.speaker_map_confirmed)
    outputs = {"json": str(output_json), **({"txt": str(output_txt)} if output_txt else {})}
    result = {
        "schema_version": 1,
        "type": "speaker-transcript",
        "created_at": datetime.now(UTC).isoformat(),
        "source": transcript["source"],
        "source_sha256": diarization["source_sha256"].lower(),
        "transcript_json": str(transcript_path),
        "transcript_sha256": transcript_sha256,
        "diarization_json": str(diarization_path),
        "diarization_sha256": diarization_sha256,
        "speaker_map_json": str(map_path) if map_path else None,
        "speaker_map_sha256": map_sha256,
        "alignment_timeline": (
            "exclusive_segments" if diarization.get("exclusive_segments") else "segments"
        ),
        "speakers": sorted({turn["speaker"] for turn in turns}),
        "turns": turns,
        "outputs": outputs,
    }
    if any(sha256_file(path) != fingerprint for path, fingerprint in fingerprints.items()):
        raise ValueError("Input JSON changed during alignment; outputs were not published")
    contents = {output_json: json.dumps(result, ensure_ascii=False, indent=2) + "\n"}
    if output_txt:
        contents[output_txt] = render_txt(turns)
    write_texts(contents)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        result = run(parser.parse_args(argv))
    except Exception as error:
        print(f"Speaker transcript failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result["outputs"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
