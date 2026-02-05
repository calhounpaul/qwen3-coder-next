# GUI-Actor Inference Container
#
# Provides natural language click prediction using GUI-Actor (Qwen2.5-VL based)
# Model downloaded from HuggingFace on first startup

FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04

LABEL maintainer="noVNC Automation"
LABEL description="GUI-Actor inference server for natural language UI interaction"

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    git \
    wget \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel

# Install PyTorch with CUDA support
RUN pip3 install --no-cache-dir \
    torch>=2.0.0 \
    torchvision>=0.15.0 \
    --index-url https://download.pytorch.org/whl/cu124

# Install ML dependencies
# Pin transformers<5.0 - v5.0 moved hidden_size to text_config breaking GUI-Actor
RUN pip3 install --no-cache-dir \
    "transformers>=4.45.0,<5.0.0" \
    accelerate>=0.25.0 \
    safetensors>=0.4.0 \
    einops>=0.7.0 \
    timm>=1.0.0 \
    qwen-vl-utils>=0.0.2

# Install image processing
RUN pip3 install --no-cache-dir \
    Pillow>=10.0.0 \
    numpy>=1.24.0

# Install FastAPI server
RUN pip3 install --no-cache-dir \
    fastapi>=0.109.0 \
    uvicorn>=0.27.0 \
    python-multipart>=0.0.6

# Create directories
RUN mkdir -p /app /app/gui_actor

# Copy GUI-Actor module (from local build context)
COPY gui_actor/ /app/gui_actor/

# Copy server
COPY server.py /app/server.py
RUN chmod +x /app/server.py

# Environment variables
ENV TRANSFORMERS_CACHE=/app/models/transformers
ENV HF_HOME=/app/models/huggingface
ENV GUI_ACTOR_PORT=8001
ENV GUI_ACTOR_HOST=0.0.0.0

# Expose port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

WORKDIR /app

ENTRYPOINT ["python3", "-u", "/app/server.py"]
