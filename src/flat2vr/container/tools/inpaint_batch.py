#!/usr/bin/env python3
"""Run M2SVid over all <=25-frame clips while keeping the model loaded."""

from __future__ import annotations

import argparse
import gc
from pathlib import Path

import ffmpeg
import numpy as np
from omegaconf import OmegaConf
from pytorch_lightning import seed_everything
import torch
from torchvision import transforms
import torchvision.io

from m2svid.data.utils import apply_closing, apply_dilation, get_video_frames
from m2svid.utils.video_utils import get_video_fps
from sgm.util import instantiate_from_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks-dir", type=Path, required=True)
    parser.add_argument("--warp-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cleanup-inputs", action="store_true")
    return parser.parse_args()


def save_video(video: torch.Tensor, fps: float, path: Path) -> None:
    frames = video.cpu().numpy().transpose(0, 2, 3, 4, 1)
    frames = np.concatenate(frames)
    frames = (((frames + 1) / 2).clip(0, 1) * 255).astype(np.uint8)
    torchvision.io.write_video(str(path), frames, fps=int(round(fps)), options={"crf": "14"})


def main() -> None:
    args = parse_args()
    chunks = sorted(args.chunks_dir.glob("chunk_*.mp4"))
    if not chunks:
        raise RuntimeError(f"No chunks found in {args.chunks_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    config = OmegaConf.load(args.model_config)
    # Training-only perceptual losses construct VGG/LPIPS and may download
    # evaluation weights. Generation does not use them.
    config.model.params.loss_fn_config = None
    model = instantiate_from_config(config.model).cpu()
    model.init_from_ckpt(str(args.checkpoint))
    model = model.cuda().half().eval()

    for index, video_path in enumerate(chunks):
        # generate(..., offload_before_decode=True) moves these large modules
        # to CPU so the temporal VAE can decode within 24 GB. Restore them for
        # conditioning and denoising of each subsequent window.
        model.model.cuda()
        model.conditioner.cuda()
        seed_everything(args.seed + index)
        reprojected_path = args.warp_dir / f"{video_path.stem}_reprojected.mp4"
        mask_path = args.warp_dir / f"{video_path.stem}_mask.mp4"

        input_video = get_video_frames(str(video_path))
        reprojected = get_video_frames(str(reprojected_path))
        reprojected_mask = get_video_frames(str(mask_path), video_is_grayscale=True)
        fps = get_video_fps(str(video_path), ffmpeg.probe(str(video_path)))

        if input_video.shape[0] > 25:
            raise RuntimeError(f"{video_path} has {input_video.shape[0]} frames; maximum is 25")
        if not (len(input_video) == len(reprojected) == len(reprojected_mask)):
            raise RuntimeError(f"Frame count mismatch for {video_path.name}")

        reprojected_mask = apply_closing(reprojected_mask, 11)
        reprojected[reprojected_mask.repeat(1, 3, 1, 1) > 0.5] = 0
        reprojected_mask = apply_dilation(reprojected_mask, 3).repeat(1, 3, 1, 1)

        input_video = input_video.permute(1, 0, 2, 3).float() * 2 - 1
        reprojected = reprojected.permute(1, 0, 2, 3).float() * 2 - 1
        reprojected_mask = reprojected_mask.permute(1, 0, 2, 3).float() * 2 - 1

        _, _, height, width = reprojected_mask.shape
        reprojected_mask = reprojected_mask.permute(1, 0, 2, 3)
        reprojected_mask = transforms.Resize(
            [height // 8, width // 8], antialias=False
        )(reprojected_mask)
        reprojected_mask = reprojected_mask[:, [0]].permute(1, 0, 2, 3).float()

        batch = {
            "video": input_video[None].cuda(),
            "video_2nd_view": input_video[None].cuda(),
            "reprojected_video": reprojected[None].cuda(),
            "reprojected_mask": reprojected_mask[None].cuda(),
            "fps_id": torch.tensor([fps]).cuda(),
            "caption": [""],
            "motion_bucket_id": torch.tensor([127]).cuda(),
        }

        with torch.inference_mode():
            generated = model.generate(batch, offload_before_decode=True)["generated-video"][0].cpu()
        sbs = torch.cat([input_video, generated], dim=-1)
        save_video(sbs[None], fps, args.output_dir / f"{video_path.stem}_sbs.mp4")

        del batch, generated, sbs, input_video, reprojected, reprojected_mask
        gc.collect()
        torch.cuda.empty_cache()

        if args.cleanup_inputs:
            video_path.unlink()
            reprojected_path.unlink()
            mask_path.unlink()


if __name__ == "__main__":
    main()
