"""Shared conversion options for all execution backends."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ConversionOptions:
    fps: int = 24
    width: int = 896
    height: int = 512
    output_height: int = 1024
    window_frames: int = 16
    depth_steps: int = 5
    disparity: float = 0.05
    quality: int = 19
    encoder: str = "auto"

    def validate(self) -> None:
        if self.fps not in (24, 25, 30):
            raise ValueError("fps must be one of 24, 25, or 30")
        if self.width <= 0 or self.width % 64:
            raise ValueError("width must be a positive multiple of 64")
        if self.height <= 0 or self.height % 64:
            raise ValueError("height must be a positive multiple of 64")
        if self.output_height <= 0 or self.output_height % 2:
            raise ValueError("output height must be a positive even integer")
        if not 8 <= self.window_frames <= 25:
            raise ValueError("window frames must be between 8 and 25")
        if not 1 <= self.depth_steps <= 20:
            raise ValueError("depth steps must be between 1 and 20")
        if not 0.01 <= self.disparity <= 0.08:
            raise ValueError("disparity must be between 0.01 and 0.08")
        if not 0 <= self.quality <= 51:
            raise ValueError("quality must be between 0 and 51")
        if self.encoder not in ("auto", "hevc_nvenc", "libx265"):
            raise ValueError("encoder must be auto, hevc_nvenc, or libx265")

    def to_dict(self) -> dict[str, int | float | str]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ConversionOptions":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown conversion options: {', '.join(unknown)}")
        result = cls(**value)  # type: ignore[arg-type]
        result.validate()
        return result

    def container_args(self) -> list[str]:
        self.validate()
        return [
            "--fps",
            str(self.fps),
            "--width",
            str(self.width),
            "--height",
            str(self.height),
            "--output-height",
            str(self.output_height),
            "--window-frames",
            str(self.window_frames),
            "--depth-steps",
            str(self.depth_steps),
            "--disparity",
            str(self.disparity),
            "--cq",
            str(self.quality),
            "--encoder",
            self.encoder,
        ]


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_Full_SBS.mp4")
