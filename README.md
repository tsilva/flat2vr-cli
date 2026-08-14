<div align="center">
  <img src="./logo.png" alt="flat2vr" width="420" />

  **🎞️ Turn flat video into headset-ready stereo depth 🥽**
</div>

flat2vr converts ordinary 2D footage into stereoscopic Full Side-by-Side HEVC
video for headsets such as Meta Quest 3. The client stays lightweight while
CUDA, FFmpeg, DepthCrafter, and M2SVid run in the same pinned GPU container on
Modal or an NVIDIA Docker host.

The Python distribution is named `flat2vr-cli`; it installs the `flat2vr`
command and `flat2vr` import package. Python 3.11 or newer is required.

## Quick start

Install the command with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install flat2vr-cli
flat2vr movie.mp4
```

Modal is the default backend. On the first interactive conversion, flat2vr
explains that cloud charges may apply, opens Modal sign-in when needed, deploys
the pinned application to your Modal account, and continues the conversion.
Later conversions reuse the deployment and approximately 13 GB model cache.

The result is written beside the input as `movie_Full_SBS.mp4`. Choose a path,
rendering profile, or stereo strength when needed:

```bash
flat2vr movie.mp4 -o headset/movie.mp4
flat2vr movie.mp4 --preset fast
flat2vr movie.mp4 --preset best --strength strong
```

Existing outputs are protected. Pass `--overwrite` when replacement is
intentional.

## Setup

Setup is safe to rerun and repairs the currently selected backend:

```bash
flat2vr setup
flat2vr setup modal --gpu L40S
```

Non-interactive Modal use requires `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`,
or an earlier interactive `flat2vr setup modal`.

To run on Docker instead, select it once and continue using the same conversion
command:

```bash
# Current Docker context or DOCKER_HOST.
flat2vr setup docker

# Minimal remote GPU host over SSH.
flat2vr setup docker ssh://gpu.example.com --sudo

# Docker API endpoint.
flat2vr setup docker tcp://gpu.example.com:2376

flat2vr movie.mp4
```

Use `flat2vr setup modal` to switch back. A missing Docker image is built
automatically; `flat2vr setup docker --rebuild` forces a clean rebuild.

## Profiles and expert controls

`balanced` is the default profile. `fast` uses one DepthCrafter denoising step
and 720-line output; `best` keeps the evidence-backed five-step depth pass and
uses lighter HEVC compression. Stereo strength can be `subtle`, `normal`, or
`strong`.

Run `flat2vr --help-advanced` for raw frame rate, model dimensions, output
height, window size, depth steps, disparity, HEVC quality, encoder, and
diagnostic work-retention controls. Explicit raw values override the selected
profile. `--verbose` shows backend commands and detailed errors.

Temporary containers, uploaded inputs, outputs, and failed-job work are cleaned
up by default. `--keep-work` retains diagnostic artifacts. Model caches persist
across jobs.

## Notes

- A 16:9 source at the balanced output height produces an approximately
  3584×1024 frame containing two full-resolution eye views.
- Automatic encoding prefers NVENC and retries with CPU `libx265` if NVENC
  fails.
- Successful output retains source audio when present and declares left/right
  stereo metadata.
- Flat2VR creates binocular depth, not six-degree-of-freedom geometry: viewers
  can perceive the 3D image but cannot move around its objects.
- Source revisions, Python environments, models, and model hashes are pinned
  for reproducibility.

## Upgrading from 0.1

The 0.2 interface intentionally removes the old command tree:

| 0.1 command | 0.2 replacement |
|---|---|
| `flat2vr convert INPUT` | `flat2vr INPUT` |
| `flat2vr doctor --backend modal` | `flat2vr setup modal` |
| `flat2vr build` | `flat2vr setup docker --rebuild` |
| `flat2vr modal deploy` | `flat2vr setup modal` |
| repeated backend flags | one remembered `flat2vr setup ...` |

## Development

```bash
git clone https://github.com/tsilva/flat2vr-cli.git
cd flat2vr-cli
uv sync
uv run python -m unittest discover -s tests -v
```

## Architecture

![flat2vr architecture](./architecture.png)
