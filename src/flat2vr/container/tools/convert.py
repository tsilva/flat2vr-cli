#!/usr/bin/env python3
"""Convert a conventional video to Quest-compatible Full-SBS stereoscopic video."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
M2SVID = ROOT / "m2svid"
DEPTH_ROOT = M2SVID / "third_party" / "DepthCrafter"
DEPTH_PYTHON = DEPTH_ROOT / ".venv" / "bin" / "python"
SGM_PYTHON = ROOT / "envs" / "sgm" / ".venv" / "bin" / "python"
MODEL_ROOT = Path(os.environ.get("FLAT2VR_MODEL_DIR", "/models"))
WORK_ROOT = Path(os.environ.get("FLAT2VR_WORK_DIR", "/work/runs"))
VERBOSE = False


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    if VERBOSE:
        print("+", " ".join(str(part) for part in command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a regular video to Full-SBS HEVC for Meta Quest 3."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps", type=int, required=True, choices=(24, 25, 30))
    parser.add_argument(
        "--width",
        type=int,
        required=True,
        help="Per-eye model width; multiple of 64",
    )
    parser.add_argument(
        "--height",
        type=int,
        required=True,
        help="Per-eye model height; multiple of 64",
    )
    parser.add_argument("--output-height", type=int, required=True)
    parser.add_argument(
        "--window-frames", type=int, required=True, choices=range(8, 26)
    )
    parser.add_argument("--depth-steps", type=int, required=True)
    parser.add_argument("--disparity", type=float, required=True)
    parser.add_argument(
        "--cq", type=int, required=True, help="NVENC constant-quality value"
    )
    parser.add_argument(
        "--encoder",
        choices=("auto", "hevc_nvenc", "libx265"),
        required=True,
    )
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def ffconcat_quote(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def main() -> None:
    global VERBOSE
    args = parse_args()
    VERBOSE = args.verbose
    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        raise SystemExit(f"Input file does not exist: {input_path}")
    if args.width % 64 or args.height % 64:
        raise SystemExit("--width and --height must both be divisible by 64")
    if not 0.01 <= args.disparity <= 0.08:
        raise SystemExit("--disparity must be between 0.01 and 0.08")

    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = WORK_ROOT / f"{input_path.stem}-{stamp}-{os.getpid()}"
    chunks_dir = run_dir / "chunks"
    warp_dir = run_dir / "warp"
    scratch_dir = run_dir / "depth-scratch"
    sbs_dir = run_dir / "sbs"
    for directory in (chunks_dir, warp_dir, scratch_dir, sbs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    try:
        print("Preparing video...", flush=True)
        scale_filter = (
            f"fps={args.fps},"
            f"scale={args.width}:{args.height}:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2:flags=lanczos,"
            f"pad={args.width}:{args.height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )
        segment_time = args.window_frames / args.fps
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                str(input_path),
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                scale_filter,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "12",
                "-pix_fmt",
                "yuv420p",
                "-g",
                str(args.window_frames),
                "-keyint_min",
                str(args.window_frames),
                "-sc_threshold",
                "0",
                "-f",
                "segment",
                "-segment_time",
                f"{segment_time:.12f}",
                "-segment_time_delta",
                f"{0.5 / args.fps:.12f}",
                "-reset_timestamps",
                "1",
                str(chunks_dir / "chunk_%06d.mp4"),
            ]
        )

        python_path = os.pathsep.join(
            [
                str(M2SVID),
                str(M2SVID / "third_party" / "Hi3D-Official"),
                str(M2SVID / "third_party" / "pytorch-msssim"),
            ]
        )
        sgm_env = os.environ.copy()
        # M2SVid is pinned to Torch 2.0, which predates expandable_segments.
        sgm_env.pop("PYTORCH_CUDA_ALLOC_CONF", None)
        sgm_env["PYTHONPATH"] = python_path
        depth_env = os.environ.copy()
        depth_env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
        depth_env["PYTHONPATH"] = str(DEPTH_ROOT)

        print("Estimating depth...", flush=True)
        run(
            [
                str(DEPTH_PYTHON),
                str(ROOT / "tools" / "depth_and_warp.py"),
                "--chunks-dir",
                str(chunks_dir),
                "--warp-dir",
                str(warp_dir),
                "--scratch-dir",
                str(scratch_dir),
                "--unet",
                str(MODEL_ROOT / "depthcrafter"),
                "--svd",
                str(MODEL_ROOT / "svd-xt"),
                "--warping-script",
                str(M2SVID / "warping.py"),
                "--sgm-python",
                str(SGM_PYTHON),
                "--depth-steps",
                str(args.depth_steps),
                "--group-size",
                str(max(1, 100 // args.window_frames)),
                "--max-res",
                str(max(args.width, args.height)),
                "--disparity",
                str(args.disparity),
            ],
            cwd=M2SVID,
            env=depth_env,
        )

        print("Synthesizing stereo view...", flush=True)
        run(
            [
                str(SGM_PYTHON),
                str(ROOT / "tools" / "inpaint_batch.py"),
                "--chunks-dir",
                str(chunks_dir),
                "--warp-dir",
                str(warp_dir),
                "--output-dir",
                str(sbs_dir),
                "--model-config",
                str(M2SVID / "configs" / "m2svid.yaml"),
                "--checkpoint",
                str(MODEL_ROOT / "m2svid" / "m2svid_weights.pt"),
                "--cleanup-inputs",
            ],
            cwd=M2SVID,
            env=sgm_env,
        )

        sbs_chunks = sorted(sbs_dir.glob("chunk_*_sbs.mp4"))
        if not sbs_chunks:
            raise RuntimeError("M2SVid produced no SBS chunks")
        concat_file = run_dir / "sbs-concat.txt"
        concat_file.write_text(
            "".join(f"file '{ffconcat_quote(path)}'\n" for path in sbs_chunks),
            encoding="utf-8",
        )

        print("Encoding Full-SBS video...", flush=True)
        common_encode = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-i",
            str(input_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-vf",
            f"scale=-2:{args.output_height}:flags=lanczos,format=yuv420p",
        ]
        common_tail = [
            "-tag:v",
            "hvc1",
            "-metadata:s:v:0",
            "stereo_mode=left_right",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        nvenc = [
            "-c:v",
            "hevc_nvenc",
            "-preset",
            "p7",
            "-tune",
            "hq",
            "-rc:v",
            "vbr",
            "-cq:v",
            str(args.cq),
            "-b:v",
            "0",
        ]
        x265 = ["-c:v", "libx265", "-preset", "slow", "-crf", str(args.cq)]
        if args.encoder == "auto":
            try:
                run(common_encode + nvenc + common_tail)
            except subprocess.CalledProcessError:
                print("NVENC encoding failed; retrying with libx265", file=sys.stderr)
                output_path.unlink(missing_ok=True)
                run(common_encode + x265 + common_tail)
        else:
            selected = nvenc if args.encoder == "hevc_nvenc" else x265
            run(common_encode + selected + common_tail)
        print(f"\nQuest 3 Full-SBS output: {output_path}")
    except Exception:
        if args.keep_work:
            print(f"Work files retained after failure: {run_dir}", file=sys.stderr)
        else:
            shutil.rmtree(run_dir, ignore_errors=True)
        raise
    else:
        if args.keep_work:
            print(f"Work files retained: {run_dir}")
        else:
            shutil.rmtree(run_dir)


if __name__ == "__main__":
    main()
