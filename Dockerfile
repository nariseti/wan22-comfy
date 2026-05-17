# Wan 2.2 ComfyUI image for Vast.ai
# Base: vastai/comfy (already cached on all Vast.ai machines — fast pull)
# Adds: WanVideo nodes + pip packages + rclone pre-baked
# Runtime: models pulled from Cloudflare R2 on boot (~30 sec)
#
# Vast.ai image name: nariseti/wan22-comfy:latest/jupyter

FROM vastai/comfy:v0.20.1-cuda-12.9-py312

ENV DEBIAN_FRONTEND=noninteractive

# ── Clone custom nodes ────────────────────────────────────────────────────────
WORKDIR /workspace/ComfyUI/custom_nodes

RUN git clone --depth=1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git && \
    git clone --depth=1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    git clone --depth=1 https://github.com/kijai/ComfyUI-KJNodes.git && \
    git clone --depth=1 https://github.com/ltdrdata/ComfyUI-Manager.git

# ── Install all pip packages ──────────────────────────────────────────────────
RUN /venv/main/bin/pip install --no-cache-dir \
        gguf ftfy accelerate opencv-python-headless imageio-ffmpeg einops \
        hf_transfer huggingface_hub \
        -r /workspace/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper/requirements.txt \
        -r /workspace/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt \
        -r /workspace/ComfyUI/custom_nodes/ComfyUI-KJNodes/requirements.txt

# ── rclone for R2 model pull on boot ─────────────────────────────────────────
RUN curl -fsSL https://rclone.org/install.sh | bash

# ── Startup hook: pull models from R2 then hand off to vastai/comfy entrypoint
COPY entrypoint.sh /wan22-entrypoint.sh
RUN sed -i 's/\r//' /wan22-entrypoint.sh && chmod +x /wan22-entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/wan22-entrypoint.sh"]
