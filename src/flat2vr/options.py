"""Authoritative conversion settings shared by every execution backend."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


PROTOCOL_VERSION = 1
PRESETS: dict[str, dict[str, int]] = {
    "fast": {"depth_steps": 1, "output_height": 720, "quality": 23},
    "balanced": {"depth_steps": 5, "output_height": 1024, "quality": 19},
    "best": {"depth_steps": 5, "output_height": 1024, "quality": 17},
}
STRENGTHS: dict[str, float] = {
    "subtle": 0.03,
    "normal": 0.05,
    "strong": 0.07,
}


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

    @classmethod
    def from_profile(
        cls,
        *,
        preset: str = "balanced",
        strength: str = "normal",
        overrides: dict[str, object] | None = None,
    ) -> "ConversionOptions":
        if preset not in PRESETS:
            raise ValueError(f"unknown preset: {preset}")
        if strength not in STRENGTHS:
            raise ValueError(f"unknown strength: {strength}")
        values: dict[str, object] = asdict(cls())
        values.update(PRESETS[preset])
        values["disparity"] = STRENGTHS[strength]
        if overrides:
            unknown = sorted(set(overrides) - set(values))
            if unknown:
                raise ValueError(f"unknown conversion overrides: {', '.join(unknown)}")
            values.update(
                (name, value) for name, value in overrides.items() if value is not None
            )
        result = cls(**values)  # type: ignore[arg-type]
        result.validate()
        return result

    def validate(self) -> None:
        integer_fields = {
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "output height": self.output_height,
            "window frames": self.window_frames,
            "depth steps": self.depth_steps,
            "quality": self.quality,
        }
        invalid_integer = next(
            (
                name
                for name, value in integer_fields.items()
                if not isinstance(value, int) or isinstance(value, bool)
            ),
            None,
        )
        if invalid_integer:
            raise ValueError(f"{invalid_integer} must be an integer")
        if not isinstance(self.disparity, (int, float)) or isinstance(
            self.disparity, bool
        ):
            raise ValueError("disparity must be a number")
        if not isinstance(self.encoder, str):
            raise ValueError("encoder must be a string")
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

    def to_request(
        self,
        *,
        verbose: bool = False,
        keep_work: bool = False,
    ) -> dict[str, object]:
        if not isinstance(verbose, bool) or not isinstance(keep_work, bool):
            raise ValueError("runtime options must be booleans")
        return {
            "protocol": PROTOCOL_VERSION,
            "options": self.to_dict(),
            "verbose": verbose,
            "keep_work": keep_work,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ConversionOptions":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown conversion options: {', '.join(unknown)}")
        result = cls(**value)  # type: ignore[arg-type]
        result.validate()
        return result

    @classmethod
    def from_request(
        cls,
        value: dict[str, object],
    ) -> tuple["ConversionOptions", bool, bool]:
        allowed = {"protocol", "options", "verbose", "keep_work"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown request fields: {', '.join(unknown)}")
        if value.get("protocol") != PROTOCOL_VERSION:
            raise ValueError(
                f"unsupported conversion protocol: {value.get('protocol')!r}; "
                f"expected {PROTOCOL_VERSION}"
            )
        raw_options = value.get("options")
        if not isinstance(raw_options, dict):
            raise ValueError("request options must be an object")
        verbose = value.get("verbose", False)
        keep_work = value.get("keep_work", False)
        if not isinstance(verbose, bool) or not isinstance(keep_work, bool):
            raise ValueError("request runtime options must be booleans")
        return cls.from_dict(raw_options), verbose, keep_work

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
