"""One-command interface for converting flat video to stereoscopic video."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import time

from flat2vr import __version__
from flat2vr.configuration import (
    Configuration,
    ConfigurationError,
    DockerConfiguration,
    load_configuration,
    save_configuration,
)
from flat2vr.docker_backend import DockerBackend
from flat2vr.modal_backend import ModalBackend
from flat2vr.options import (
    PRESETS,
    STRENGTHS,
    ConversionOptions,
    default_output_path,
)
from flat2vr.process import CommandError


ADVANCED_ARGUMENTS = (
    ("--fps", {"type": int, "choices": (24, 25, 30), "help": "output FPS"}),
    ("--width", {"type": int, "help": "per-eye model width; multiple of 64"}),
    ("--height", {"type": int, "help": "per-eye model height; multiple of 64"}),
    ("--output-height", {"type": int, "help": "final Full-SBS frame height"}),
    ("--window-frames", {"type": int, "help": "frames per synthesis window"}),
    ("--depth-steps", {"type": int, "help": "DepthCrafter denoising steps"}),
    ("--disparity", {"type": float, "help": "stereo disparity from 0.01 to 0.08"}),
    ("--quality", {"type": int, "help": "HEVC quality from 0 (best) to 51"}),
    (
        "--encoder",
        {
            "choices": ("auto", "hevc_nvenc", "libx265"),
            "help": "HEVC encoder",
        },
    ),
)


def build_parser(*, advanced: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flat2vr",
        description="Turn a flat video into headset-ready Full-SBS 3D.",
        epilog=(
            "examples:\n"
            "  flat2vr movie.mp4\n"
            "  flat2vr movie.mp4 --preset best --strength strong\n"
            "  flat2vr setup\n"
            "  flat2vr setup docker ssh://gpu.example.com --sudo"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", type=Path, nargs="?", help="video to convert")
    parser.add_argument("-o", "--output", type=Path, help="output .mp4 path")
    parser.add_argument(
        "--preset",
        choices=tuple(PRESETS),
        default="balanced",
        help="speed and output quality (default: balanced)",
    )
    parser.add_argument(
        "--strength",
        choices=tuple(STRENGTHS),
        default="normal",
        help="stereo depth strength (default: normal)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing output file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show commands and detailed errors",
    )
    parser.add_argument(
        "--help-advanced",
        action="store_true",
        help="show expert conversion controls",
    )
    hidden = None if advanced else argparse.SUPPRESS
    for flag, keyword_arguments in ADVANCED_ARGUMENTS:
        arguments = dict(keyword_arguments)
        if hidden is not None:
            arguments["help"] = hidden
        parser.add_argument(flag, default=None, **arguments)
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help=(
            "retain backend work files for diagnosis"
            if advanced
            else argparse.SUPPRESS
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"flat2vr {__version__}",
    )
    return parser


def build_setup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flat2vr setup",
        description="Set up or repair the selected GPU backend.",
    )
    parser.add_argument("backend", nargs="?", choices=("modal", "docker"))
    parser.add_argument(
        "target",
        nargs="?",
        help="Docker ssh:// or daemon URL; omit for the normal Docker context",
    )
    parser.add_argument("--gpu", help="Modal GPU type (default: L40S)")
    parser.add_argument(
        "--sudo",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="use sudo -n for an SSH Docker target",
    )
    parser.add_argument("--model-path", help="host model cache path for Docker")
    parser.add_argument(
        "--clear-model-path",
        action="store_true",
        help="return Docker to its managed model volume",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="rebuild the Docker image without cache",
    )
    parser.add_argument("--verbose", action="store_true", help="show backend commands")
    return parser


def _conversion_options(args: argparse.Namespace) -> ConversionOptions:
    overrides = {
        name.removeprefix("--").replace("-", "_"): getattr(
            args, name.removeprefix("--").replace("-", "_")
        )
        for name, _ in ADVANCED_ARGUMENTS
    }
    return ConversionOptions.from_profile(
        preset=args.preset,
        strength=args.strength,
        overrides=overrides,
    )


def _docker_backend(configuration: DockerConfiguration) -> DockerBackend:
    if (configuration.target or "").startswith("ssh://"):
        return DockerBackend(
            docker_ssh=configuration.target.removeprefix("ssh://"),
            docker_sudo=configuration.sudo,
            model_path=configuration.model_path,
        )
    return DockerBackend(
        docker_host=configuration.target,
        model_path=configuration.model_path,
    )


def _confirm_first_modal_setup() -> None:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Flat2VR is not set up; run `flat2vr setup modal` before converting "
            "in a non-interactive environment"
        )
    print(
        "First-time setup uses your Modal account. Your video will be uploaded "
        "there and GPU charges may apply."
    )
    answer = input("Continue? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise RuntimeError("setup cancelled")


def dispatch_conversion(args: argparse.Namespace) -> Path | None:
    if args.help_advanced:
        build_parser(advanced=True).print_help()
        return None
    if args.input is None:
        build_parser().print_help()
        return None

    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"input video does not exist: {input_path}")
    output_path = (args.output or default_output_path(input_path)).expanduser().resolve()
    if output_path == input_path:
        raise ValueError("output path must be different from the input video")
    if output_path.suffix.lower() != ".mp4":
        raise ValueError("output path must end in .mp4")
    if output_path.exists() and not args.overwrite:
        raise FileExistsError(
            f"output already exists: {output_path}; pass --overwrite to replace it"
        )
    options = _conversion_options(args)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    saved = load_configuration()
    configuration = saved or Configuration()
    started = time.monotonic()
    if configuration.backend == "modal":
        if saved is None:
            _confirm_first_modal_setup()
        backend = ModalBackend(gpu=configuration.modal_gpu)
        backend.setup(interactive=saved is None, verbose=args.verbose)
        if saved is None:
            save_configuration(configuration)
        backend.convert(
            input_path,
            output_path,
            options,
            verbose=args.verbose,
            keep_work=args.keep_work,
        )
    else:
        _docker_backend(configuration.docker).convert(
            input_path,
            output_path,
            options,
            verbose=args.verbose,
            keep_work=args.keep_work,
        )
    elapsed = time.monotonic() - started
    print(f"Created {output_path} in {elapsed / 60:.1f} minutes")
    return output_path


def dispatch_setup(args: argparse.Namespace) -> Configuration:
    current = load_configuration()
    selected_backend = args.backend or (current.backend if current else "modal")
    base = current or Configuration()

    if selected_backend == "modal":
        if args.target is not None or args.sudo is not None or args.model_path is not None:
            raise ValueError("Docker target options require `flat2vr setup docker`")
        if args.clear_model_path or args.rebuild:
            raise ValueError("Docker maintenance options require `flat2vr setup docker`")
        gpu = args.gpu or base.modal_gpu
        result = replace(base, backend="modal", modal_gpu=gpu)
        ModalBackend(gpu=gpu).setup(
            interactive=sys.stdin.isatty(),
            verbose=args.verbose,
        )
    else:
        if args.gpu:
            raise ValueError("--gpu requires `flat2vr setup modal`")
        if args.clear_model_path and args.model_path is not None:
            raise ValueError("use either --model-path or --clear-model-path, not both")
        old = base.docker
        target = args.target if args.target is not None else old.target
        sudo = args.sudo if args.sudo is not None else old.sudo
        if args.clear_model_path:
            model_path = None
        else:
            model_path = args.model_path if args.model_path is not None else old.model_path
        docker = DockerConfiguration(target=target, sudo=sudo, model_path=model_path)
        docker.validate()
        result = replace(base, backend="docker", docker=docker)
        _docker_backend(docker).setup(rebuild=args.rebuild, verbose=args.verbose)

    path = save_configuration(result)
    print(f"Saved setup in {path}")
    return result


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    verbose = "--verbose" in arguments
    try:
        if arguments and arguments[0] == "setup":
            dispatch_setup(build_setup_parser().parse_args(arguments[1:]))
        else:
            dispatch_conversion(build_parser().parse_args(arguments))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130) from None
    except (
        CommandError,
        ConfigurationError,
        FileExistsError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        if verbose:
            raise
        print(f"flat2vr: error: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
