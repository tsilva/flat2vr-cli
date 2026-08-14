"""Command-line interface for flat2vr."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

from flat2vr import __version__
from flat2vr.docker_backend import (
    DEFAULT_IMAGE,
    DEFAULT_MODEL_VOLUME as DEFAULT_DOCKER_MODEL_VOLUME,
    DockerBackend,
)
from flat2vr.modal_backend import (
    DEFAULT_APP,
    DEFAULT_GPU,
    DEFAULT_JOBS_VOLUME,
    DEFAULT_MODEL_VOLUME as DEFAULT_MODAL_MODEL_VOLUME,
    ModalBackend,
    deploy_modal,
)
from flat2vr.options import ConversionOptions, default_output_path
from flat2vr.process import CommandError


def _conversion_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fps", type=int, default=24, choices=(24, 25, 30))
    parser.add_argument("--width", type=int, default=896, help="per-eye model width")
    parser.add_argument("--height", type=int, default=512, help="per-eye model height")
    parser.add_argument("--output-height", type=int, default=1024)
    parser.add_argument("--window-frames", type=int, default=16)
    parser.add_argument("--depth-steps", type=int, default=5)
    parser.add_argument("--disparity", type=float, default=0.05)
    parser.add_argument("--quality", type=int, default=19, help="HEVC quality, 0-51")
    parser.add_argument(
        "--encoder",
        choices=("auto", "hevc_nvenc", "libx265"),
        default="auto",
    )


def _docker_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Docker backend")
    group.add_argument("--docker-host", default=os.environ.get("DOCKER_HOST"))
    group.add_argument(
        "--docker-ssh",
        default=os.environ.get("FLAT2VR_DOCKER_SSH"),
        metavar="HOST",
        help="run Docker through ssh HOST (for example beast-3.nord)",
    )
    group.add_argument(
        "--docker-sudo",
        action="store_true",
        default=os.environ.get("FLAT2VR_DOCKER_SUDO") == "1",
        help="run remote Docker as sudo -n docker",
    )
    group.add_argument(
        "--image",
        default=os.environ.get("FLAT2VR_IMAGE", DEFAULT_IMAGE),
    )
    group.add_argument(
        "--model-volume",
        default=os.environ.get("FLAT2VR_MODEL_VOLUME", DEFAULT_DOCKER_MODEL_VOLUME),
    )
    group.add_argument(
        "--model-path",
        default=os.environ.get("FLAT2VR_MODEL_PATH"),
        help="host path to bind at /models instead of a Docker volume",
    )


def _modal_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_gpu: bool = False,
) -> None:
    group = parser.add_argument_group("Modal backend")
    group.add_argument(
        "--modal-app",
        default=os.environ.get("FLAT2VR_MODAL_APP", DEFAULT_APP),
    )
    group.add_argument(
        "--modal-model-volume",
        default=os.environ.get("FLAT2VR_MODAL_MODEL_VOLUME", DEFAULT_MODAL_MODEL_VOLUME),
    )
    group.add_argument(
        "--modal-jobs-volume",
        default=os.environ.get("FLAT2VR_MODAL_JOBS_VOLUME", DEFAULT_JOBS_VOLUME),
    )
    if include_gpu:
        group.add_argument(
            "--modal-gpu",
            default=os.environ.get("FLAT2VR_MODAL_GPU", DEFAULT_GPU),
        )


def _options(args: argparse.Namespace) -> ConversionOptions:
    return ConversionOptions(
        fps=args.fps,
        width=args.width,
        height=args.height,
        output_height=args.output_height,
        window_frames=args.window_frames,
        depth_steps=args.depth_steps,
        disparity=args.disparity,
        quality=args.quality,
        encoder=args.encoder,
    )


def _docker(args: argparse.Namespace) -> DockerBackend:
    return DockerBackend(
        docker_host=None if args.docker_ssh else args.docker_host,
        docker_ssh=args.docker_ssh,
        docker_sudo=args.docker_sudo,
        image=args.image,
        model_volume=args.model_volume,
        model_path=args.model_path,
    )


def _modal_backend(args: argparse.Namespace) -> ModalBackend:
    return ModalBackend(
        app_name=args.modal_app,
        model_volume=args.modal_model_volume,
        jobs_volume=args.modal_jobs_volume,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flat2vr",
        description="Convert flat video to Quest-compatible Full-SBS 3D video.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"flat2vr {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser("convert", help="convert a video")
    convert.add_argument("input", type=Path)
    convert.add_argument("-o", "--output", type=Path)
    convert.add_argument(
        "--backend",
        choices=("docker", "modal"),
        default=os.environ.get("FLAT2VR_BACKEND", "docker"),
    )
    convert.add_argument(
        "--rebuild",
        action="store_true",
        help="rebuild the Docker image",
    )
    convert.add_argument("--keep-container", action="store_true")
    _conversion_arguments(convert)
    _docker_arguments(convert)
    _modal_arguments(convert)

    build = subparsers.add_parser("build", help="build the GPU Docker image")
    build.add_argument(
        "--rebuild",
        action="store_true",
        help="disable Docker's build cache",
    )
    _docker_arguments(build)

    doctor = subparsers.add_parser("doctor", help="check a backend")
    doctor.add_argument(
        "--backend",
        choices=("docker", "modal"),
        default=os.environ.get("FLAT2VR_BACKEND", "docker"),
    )
    _docker_arguments(doctor)
    _modal_arguments(doctor)

    modal_parser = subparsers.add_parser("modal", help="manage the Modal deployment")
    modal_commands = modal_parser.add_subparsers(dest="modal_command", required=True)
    deploy = modal_commands.add_parser("deploy", help="build and deploy the Modal app")
    _modal_arguments(deploy, include_gpu=True)
    return parser


def dispatch(args: argparse.Namespace) -> None:
    if args.command == "convert":
        input_path = args.input.expanduser().resolve()
        output_path = (
            args.output or default_output_path(input_path)
        ).expanduser().resolve()
        options = _options(args)
        if args.backend == "docker":
            _docker(args).convert(
                input_path,
                output_path,
                options,
                rebuild=args.rebuild,
                keep_container=args.keep_container,
            )
        else:
            _modal_backend(args).convert(input_path, output_path, options)
        print(f"Created {output_path}")
    elif args.command == "build":
        _docker(args).build(rebuild=args.rebuild)
    elif args.command == "doctor":
        (_docker(args) if args.backend == "docker" else _modal_backend(args)).doctor()
    elif args.command == "modal" and args.modal_command == "deploy":
        deploy_modal(
            app_name=args.modal_app,
            model_volume=args.modal_model_volume,
            jobs_volume=args.modal_jobs_volume,
            gpu=args.modal_gpu,
        )
    else:  # pragma: no cover - argparse ensures this cannot occur
        raise AssertionError(f"unhandled command: {args.command}")


def main() -> None:
    try:
        dispatch(build_parser().parse_args())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130) from None
    except (
        CommandError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"flat2vr: error: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
