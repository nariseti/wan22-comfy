# Custom ComfyUI image for Vast.ai — Wan 2.2 nodes pre-baked
# Base: public NVIDIA CUDA image (buildable anywhere, including GitHub Actions)
#
# VAST.AI IMAGE NAME (in render_pipeline.py DOCKER_IMAGE):
#   nariseti/wan22-comfy:latest/jupyter
#
# BAKED IN:  ComfyUI + custom nodes + all pip packages
# RUNTIME:   model weights (too large; downloaded on first boot or via network volume)

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ── System packages (Ubuntu 24.04 has Python 3.12 natively) ──────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3.12-dev python3-pip \
        git curl wget openssh-server supervisor \
        libgl1 libglib2.0-0 ffmpeg \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Python venv at /venv/main (matches Vast.ai path convention) ───────────────
RUN python3.12 -m venv /venv/main
ENV PATH="/venv/main/bin:$PATH"

# ── Install PyTorch (CUDA 12.4) ───────────────────────────────────────────────
RUN pip install --no-cache-dir \
        torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu124

# ── Install ComfyUI ───────────────────────────────────────────────────────────
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI
RUN pip install --no-cache-dir -r /workspace/ComfyUI/requirements.txt

# ── Clone custom nodes ────────────────────────────────────────────────────────
WORKDIR /workspace/ComfyUI/custom_nodes

RUN git clone --depth=1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git && \
    git clone --depth=1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git && \
    git clone --depth=1 https://github.com/kijai/ComfyUI-KJNodes.git && \
    git clone --depth=1 https://github.com/ltdrdata/ComfyUI-Manager.git

# ── Install all pip packages in one shot ──────────────────────────────────────
RUN pip install --no-cache-dir \
        gguf ftfy accelerate "opencv-python-headless" imageio-ffmpeg einops \
        hf_transfer huggingface_hub \
        -r /workspace/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper/requirements.txt \
        -r /workspace/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt \
        -r /workspace/ComfyUI/custom_nodes/ComfyUI-KJNodes/requirements.txt

# ── SSH setup (Vast.ai needs SSH access) ─────────────────────────────────────
RUN mkdir /var/run/sshd && \
    echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config && \
    echo 'PasswordAuthentication no' >> /etc/ssh/sshd_config

# ── Supervisor: manages ComfyUI + SSH as services ────────────────────────────
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

WORKDIR /workspace
EXPOSE 18188 22

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
