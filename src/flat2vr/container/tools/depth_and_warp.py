#!/usr/bin/env python3
"""Generate temporally consistent depth in 100-frame groups, then warp each clip."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np

from depthcrafter.inference import DepthCrafterInference


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def frame_count(path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_frames",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if not value or value == "N/A":
        raise RuntimeError(f"Could not determine frame count for {path}")
    return int(value)


def ffconcat_quote(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks-dir", type=Path, required=True)
    parser.add_argument("--warp-dir", type=Path, required=True)
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--unet", type=Path, required=True)
    parser.add_argument("--svd", type=Path, required=True)
    parser.add_argument("--warping-script", type=Path, required=True)
    parser.add_argument("--sgm-python", type=Path, required=True)
    parser.add_argument("--group-size", type=int, default=4)
    parser.add_argument("--depth-steps", type=int, default=5)
    parser.add_argument("--max-res", type=int, default=512)
    parser.add_argument("--disparity", type=float, default=0.05)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    chunks = sorted(args.chunks_dir.glob("chunk_*.mp4"))
    if not chunks:
        raise RuntimeError(f"No chunks found in {args.chunks_dir}")

    args.warp_dir.mkdir(parents=True, exist_ok=True)
    args.scratch_dir.mkdir(parents=True, exist_ok=True)

    depth = DepthCrafterInference(
        unet_path=str(args.unet),
        pre_train_path=str(args.svd),
        cpu_offload="model",
    )

    for group_index in range(0, len(chunks), args.group_size):
        group = chunks[group_index : group_index + args.group_size]
        group_name = f"depth_group_{group_index // args.group_size:06d}"
        group_video = args.scratch_dir / f"{group_name}.mp4"
        concat_file = args.scratch_dir / f"{group_name}.txt"
        concat_file.write_text(
            "".join(f"file '{ffconcat_quote(path)}'\n" for path in group),
            encoding="utf-8",
        )
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-an",
                "-c:v",
                "copy",
                str(group_video),
            ]
        )

        expected_counts = [frame_count(path) for path in group]
        depth.infer(
            str(group_video),
            args.depth_steps,
            1.0,
            save_folder=str(args.scratch_dir),
            window_size=110,
            process_length=-1,
            overlap=25,
            max_res=args.max_res,
            dataset="open",
            target_fps=-1,
            seed=42,
            track_time=True,
            save_npz=True,
            save_exr=False,
        )

        group_stem = group_video.stem
        group_npz = args.scratch_dir / f"{group_stem}.npz"
        group_depth = np.load(group_npz)["depth"]
        expected_total = sum(expected_counts)
        if len(group_depth) != expected_total:
            raise RuntimeError(
                f"Depth frame mismatch for {group_name}: got {len(group_depth)}, "
                f"expected {expected_total}"
            )

        offset = 0
        for chunk, count in zip(group, expected_counts):
            chunk_npz = args.scratch_dir / f"{chunk.stem}.npz"
            np.savez_compressed(chunk_npz, depth=group_depth[offset : offset + count])
            offset += count

            reprojected = args.warp_dir / f"{chunk.stem}_reprojected.mp4"
            mask = args.warp_dir / f"{chunk.stem}_mask.mp4"
            run(
                [
                    str(args.sgm_python),
                    str(args.warping_script),
                    "--video_path",
                    str(chunk),
                    "--depth_path",
                    str(chunk_npz),
                    "--output_path_reprojected",
                    str(reprojected),
                    "--output_path_mask",
                    str(mask),
                    "--disparity_perc",
                    str(args.disparity),
                ]
            )
            chunk_npz.unlink()

        del group_depth
        for suffix in (".mp4", ".npz", "_depth.mp4", "_vis.mp4", "_input.mp4", ".txt"):
            candidate = args.scratch_dir / f"{group_stem}{suffix}"
            if candidate.exists():
                candidate.unlink()
        depth.clear_cache()


if __name__ == "__main__":
    main()
