FROM ghcr.io/astral-sh/uv:0.11.21 AS uv

FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ARG DEBIAN_FRONTEND=noninteractive
ENV UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    FLAT2VR_MODEL_DIR=/models \
    FLAT2VR_WORK_DIR=/work/runs

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates ffmpeg git libgl1 libglib2.0-0 libsm6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

WORKDIR /opt/flat2vr
ARG M2SVID_COMMIT=11b0133093d6abfcc6ff953890edf05457975318
ARG DEPTHCRAFTER_COMMIT=e5186120a71ecb56b69f9e89654198b4eadfaf60
ARG MSSSIM_COMMIT=b057b072dd869ae3f6b88543786f44d008315f69
RUN git clone https://github.com/google-research/m2svid.git m2svid \
    && git -C m2svid checkout "$M2SVID_COMMIT" \
    && git -C m2svid submodule update --init --recursive \
    && test "$(git -C m2svid/third_party/DepthCrafter rev-parse HEAD)" = "$DEPTHCRAFTER_COMMIT" \
    && test "$(git -C m2svid/third_party/pytorch-msssim rev-parse HEAD)" = "$MSSSIM_COMMIT"

COPY locks/depthcrafter/pyproject.toml locks/depthcrafter/uv.lock \
    /opt/flat2vr/m2svid/third_party/DepthCrafter/
RUN cd /opt/flat2vr/m2svid/third_party/DepthCrafter \
    && uv sync --frozen --no-dev

COPY locks/sgm/ /opt/flat2vr/envs/sgm/
RUN cd /opt/flat2vr/envs/sgm \
    && uv sync --frozen --no-dev

COPY patches/m2svid-inference.patch /tmp/m2svid-inference.patch
RUN git -C /opt/flat2vr/m2svid apply /tmp/m2svid-inference.patch \
    && rm /tmp/m2svid-inference.patch \
    && ln -s /models/m2svid /opt/flat2vr/m2svid/ckpts

COPY tools/ /opt/flat2vr/tools/
COPY bin/ /opt/flat2vr/bin/
RUN chmod 0755 /opt/flat2vr/bin/convert \
    && mkdir -p /models /work/runs \
    && /opt/flat2vr/envs/sgm/.venv/bin/python -m py_compile /opt/flat2vr/tools/*.py

VOLUME ["/models"]
CMD ["/opt/flat2vr/bin/convert", "--help"]
