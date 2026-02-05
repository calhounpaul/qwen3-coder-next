# VLM Docker container for Qwen3-VL-4B
# Uses llama-server-cuda as base, downloads model on first run

FROM llama-server-cuda:latest

# Install hf CLI for model download
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    curl \
    && pip3 install --no-cache-dir 'huggingface_hub[cli]' \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy download script
COPY download_models.sh /app/download_models.sh
RUN chmod +x /app/download_models.sh

# Create model directory
RUN mkdir -p /app/models

# Environment variables for model config
ENV VLM_MODEL_REPO="unsloth/Qwen3-VL-4B-Instruct-GGUF"
ENV VLM_MODEL_FILE="Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
ENV VLM_MMPROJ_FILE="mmproj-F16.gguf"
ENV VLM_MODEL_DIR="/app/models"

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=120s \
    CMD curl -sf http://localhost:8080/health || exit 1

# Entrypoint downloads models if missing, then starts server
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
