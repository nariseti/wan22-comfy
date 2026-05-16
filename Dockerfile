# Custom ComfyUI image with Wan 2.2 nodes pre-baked
# Base: Vast.ai's ComfyUI image (Python 3.12, CUDA 12.9)
#
# BUILD:
#   docker build -t chanu2/wan22-comfy:latest .
#   docker push chanu2/wan22-comfy:latest
#
# VAST.AI IMAGE NAME (in render_pipeline.py DOCKER_IMAGE):
#   chanu2/wan22-comfy:latest/jupyter
#
# What this bakes in vs. what stays at runtime:
#   BAKED:   custom nodes + all pip packages  (saves ~10 min + $5 setup cost per outbid)
#   RUNTIME: model weights (too large; use network volume or download on first boot)

FROM vastai/comfy_v0.20.1-cuda-12.9-py312:latest

# â”€â”€ Clone custom nodes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
WORKDIR /workspace/ComfyUI/custom_nodes

RUN git clone --depth=1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git && \
    git clone --depth=1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    git clone --depth=1 https://github.com/kijai/ComfyUI-KJNodes.git

# â”€â”€ Install pip packages in one shot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Extra packages WanVideoWrapper needs but doesn't declare in requirements.txt
RUN /venv/main/bin/pip install --no-cache-dir \
        gguf \
        ftfy \
        accelerate \
        "opencv-python-headless" \
        imageio-ffmpeg \
        einops \
        -r /workspace/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper/requirements.txt \
        -r /workspace/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt \
        -r /workspace/ComfyUI/custom_nodes/ComfyUI-KJNodes/requirements.txt

# Return to workspace root (matches Vast.ai convention)
WORKDIR /workspace
