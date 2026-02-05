"""GUI-Actor inference server.

FastAPI server for natural language click prediction using GUI-Actor (Qwen2.5-VL based).
Model downloaded from HuggingFace on first startup.
"""

import os

# GPU is assigned via docker-compose.yml device_ids: ['1']
# Docker maps that GPU to appear as GPU 0 inside the container
# Do NOT set CUDA_VISIBLE_DEVICES here - it would look for a non-existent GPU

import io
import time
import warnings
from pathlib import Path

import torch
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel

warnings.filterwarnings("ignore")

app = FastAPI(title="GUI-Actor API", version="1.0.0")

# Model config
MODEL_NAME = "microsoft/GUI-Actor-3B-Qwen2.5-VL"


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    device: str


class ClickResponse(BaseModel):
    success: bool
    x_ratio: float
    y_ratio: float
    x_pixel: int
    y_pixel: int
    image_width: int
    image_height: int
    output_text: str | None
    topk_points: list[list[float]]
    topk_values: list[float] | None
    processing_time_ms: int


class ModelState:
    def __init__(self):
        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.model = None
        self.tokenizer = None
        self.processor = None
        self.model_loaded = False

    def load(self):
        """Load the GUI-Actor model."""
        if self.model_loaded:
            return

        from transformers import AutoProcessor
        from gui_actor.modeling_qwen25vl import (
            Qwen2_5_VLForConditionalGenerationWithPointer,
        )

        print(f"INFO: Loading GUI-Actor model on {self.device}...")

        # Determine dtype
        if torch.cuda.is_available():
            major, _ = torch.cuda.get_device_capability()
            dtype = torch.bfloat16 if major >= 8 else torch.float16
        else:
            dtype = torch.float32

        # Load processor and tokenizer
        self.processor = AutoProcessor.from_pretrained(MODEL_NAME)
        self.tokenizer = self.processor.tokenizer

        # Load model
        self.model = Qwen2_5_VLForConditionalGenerationWithPointer.from_pretrained(
            MODEL_NAME,
            torch_dtype=dtype,
            attn_implementation="sdpa",
        ).eval()
        self.model.to(self.device)

        self.model_loaded = True
        print("INFO: GUI-Actor model loaded successfully")


state = ModelState()


@app.on_event("startup")
async def startup_event():
    """Pre-load model on startup."""
    print(f"INFO: Starting GUI-Actor server on {state.device}")
    try:
        state.load()
    except Exception as e:
        print(f"WARNING: Failed to pre-load model: {e}")
        print("INFO: Model will be loaded on first request")


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        model_loaded=state.model_loaded,
        device=state.device,
    )


@app.get("/ready")
async def ready():
    """Readiness check - returns 200 only when model is loaded."""
    if not state.model_loaded:
        raise HTTPException(status_code=503, detail="Model not yet loaded")
    return {"status": "ready"}


@app.post("/predict", response_model=ClickResponse)
async def predict(
    file: UploadFile = File(...),
    instruction: str = Form(...),
    max_pixels: int = Form(default=3200 * 1800),
):
    """Predict click coordinates from image and instruction."""
    start_time = time.time()

    if not state.model_loaded:
        state.load()

    from gui_actor.inference import inference

    # Load image
    contents = await file.read()
    image = Image.open(io.BytesIO(contents))
    if image.mode != "RGB":
        image = image.convert("RGB")

    original_w, original_h = image.size

    # Resize if needed
    w, h = image.size
    if w * h > max_pixels:
        resize_ratio = (max_pixels / (w * h)) ** 0.5
        new_w = int(w * resize_ratio)
        new_h = int(h * resize_ratio)
        image = image.resize((new_w, new_h))

    # Prepare conversation
    conversation = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "You are a GUI agent. Given a screenshot of the current GUI "
                        "and a human instruction, your task is to locate the screen "
                        "element that corresponds to the instruction. Output a PyAutoGUI "
                        "action with a special token that points to the correct location."
                    ),
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": instruction},
            ],
        },
    ]

    # Run inference
    with torch.inference_mode():
        pred = inference(
            conversation,
            state.model,
            state.tokenizer,
            state.processor,
            use_placeholder=True,
            topk=3,
        )

    # Extract coordinates
    x_ratio, y_ratio = pred["topk_points"][0]
    x_pixel = int(x_ratio * original_w)
    y_pixel = int(y_ratio * original_h)

    processing_time = int((time.time() - start_time) * 1000)

    return ClickResponse(
        success=True,
        x_ratio=x_ratio,
        y_ratio=y_ratio,
        x_pixel=x_pixel,
        y_pixel=y_pixel,
        image_width=original_w,
        image_height=original_h,
        output_text=pred.get("output_text"),
        topk_points=pred.get("topk_points", []),
        topk_values=pred.get("topk_values"),
        processing_time_ms=processing_time,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
