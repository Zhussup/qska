"""Tiny CLI for running the pipeline without the API.

Examples:
  python -m app.run_pipeline extract --image <path>
  python -m app.run_pipeline gaps --drawing-id <id>
  python -m app.run_pipeline estimate --drawing-id <id> --xlsx out.xlsx
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, extractor, gap_detector, estimate_engine
from .schemas import DrawingJSON


def _bootstrap() -> None:
    """Read .env once on import so the CLI works the same as the API."""
    config.load_env()


def cmd_extract(args: argparse.Namespace) -> int:
    image_path = Path(args.image)
    if not image_path.exists():
        print(f"image not found: {image_path}", file=sys.stderr)
        return 2
    drawing_id = args.drawing_id or image_path.stem
    print(f"[1/3] pass1 on {image_path.name}...", flush=True)
    facts, p1_meta, p1_text = extractor.run_pass1(image_path, args.model)
    print(f"      pass1: {p1_meta['latency_s']}s, "
          f"tokens in/out: {p1_meta['prompt_tokens']}/{p1_meta['output_tokens']}")

    print(f"[2/3] pass2...", flush=True)
    drawing, p2_meta, p2_text = extractor.run_pass2(image_path, facts, args.model)
    print(f"      pass2: {p2_meta['latency_s']}s, "
          f"tokens in/out: {p2_meta['prompt_tokens']}/{p2_meta['output_tokens']}")

    out_dir = config.ARTIFACTS_DIR / "json"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{drawing_id}__pass1.json").write_text(
        facts.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{drawing_id}__pass2.json").write_text(
        drawing.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/3] wrote {out_dir}/{drawing_id}__pass{{1,2}}.json")
    return 0


def cmd_gaps(args: argparse.Namespace) -> int:
    json_path = config.ARTIFACTS_DIR / "json" / f"{args.drawing_id}__pass2.json"
    drawing = DrawingJSON.model_validate_json(json_path.read_text(encoding="utf-8"))
    # image_path is recoverable from drawing_id (same logic as API).
    from .main import _find_image_for
    image_path = _find_image_for(args.drawing_id)
    if image_path is None:
        print("no image for that drawing_id", file=sys.stderr)
        return 2
    w, h = extractor.image_size(image_path)
    report = gap_detector.detect_gaps(drawing, str(image_path), w, h)
    out = config.ARTIFACTS_DIR / "gaps" / f"{args.drawing_id}__gaps.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"score={report.total_score}  gaps={len(report.gaps)}  -> {out}")
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    json_path = config.ARTIFACTS_DIR / "json" / f"{args.drawing_id}__pass2.json"
    drawing = DrawingJSON.model_validate_json(json_path.read_text(encoding="utf-8"))
    result = estimate_engine.estimate(drawing)
    print(f"lines={len(result.lines)}  total={result.total} {result.currency}")
    for ln in result.lines:
        print(f"  {ln.gesn_code:<20} {ln.unit:<5} qty={ln.quantity:<8} {ln.description[:80]}")
    if args.xlsx:
        estimate_engine.export_excel(result, args.xlsx)
        print(f"xlsx -> {args.xlsx}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _bootstrap()
    p = argparse.ArgumentParser(prog="qsmeta")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="Run two-pass extraction on one image")
    pe.add_argument("--image", required=True)
    pe.add_argument("--drawing-id", default=None)
    pe.add_argument("--model", default=None)
    pe.set_defaults(func=cmd_extract)

    pg = sub.add_parser("gaps", help="Run gap detector on a stored pass2 JSON")
    pg.add_argument("--drawing-id", required=True)
    pg.set_defaults(func=cmd_gaps)

    p_ = sub.add_parser("estimate", help="Derive estimate lines from a stored pass2 JSON")
    p_.add_argument("--drawing-id", required=True)
    p_.add_argument("--xlsx", default=None)
    p_.set_defaults(func=cmd_estimate)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
