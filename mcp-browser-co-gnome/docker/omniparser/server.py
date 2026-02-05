"""OmniParser v2 inference server.

FastAPI server for UI element detection using YOLO + EasyOCR + Florence-2.
Weights are downloaded from HuggingFace on first startup.
"""

import os

# GPU is assigned via docker-compose.yml device_ids: ['1']
# Docker maps that GPU to appear as GPU 0 inside the container
# Do NOT set CUDA_VISIBLE_DEVICES here - it would look for a non-existent GPU

import io
import json
import time
import warnings
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import torch
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel
from torchvision.transforms import ToPILImage

warnings.filterwarnings("ignore")

app = FastAPI(title="OmniParser v2 API", version="1.0.0")

# Weights directory inside container
WEIGHTS_DIR = Path("/app/weights")
OUTPUT_DIR = Path("/app/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class HealthResponse(BaseModel):
    status: str
    models_loaded: bool
    device: str


class Element(BaseModel):
    id: int
    box_2d: list[int]
    box_2d_normalized: list[float]
    type: str
    text: str | None
    description: str | None
    confidence: float
    center_pixel: list[int]
    center_normalized: list[float]


class AnalyzeResponse(BaseModel):
    success: bool
    image_width: int
    image_height: int
    element_count: int
    elements: list[Element]
    annotated_image_base64: str
    processing_time_ms: int


# Global model state
class ModelState:
    def __init__(self):
        # Use torch.device objects like the official handler
        if torch.cuda.is_available():
            self.device = torch.device("cuda")
        else:
            self.device = torch.device("cpu")
        self.yolo_model = None
        self.caption_model = None
        self.caption_processor = None
        self.ocr = None
        self.models_loaded = False

    def ensure_weights(self):
        """Download weights if not present."""
        if not WEIGHTS_DIR.exists() or not (WEIGHTS_DIR / "icon_detect").exists():
            from huggingface_hub import snapshot_download

            print("INFO: Downloading OmniParser weights...")
            snapshot_download(
                repo_id="microsoft/OmniParser-v2.0",
                local_dir=str(WEIGHTS_DIR),
            )
            print(f"INFO: Weights downloaded to {WEIGHTS_DIR}")

    def load_yolo(self):
        if self.yolo_model is not None:
            return
        self.ensure_weights()
        from ultralytics import YOLO

        print("INFO: Loading YOLO detection model...")
        self.yolo_model = YOLO(str(WEIGHTS_DIR / "icon_detect" / "model.pt"))
        print("INFO: YOLO loaded.")

    def load_caption(self):
        if self.caption_model is not None:
            return
        if self.caption_model == "disabled":
            return
        self.ensure_weights()
        try:
            from transformers import AutoProcessor, AutoModelForCausalLM
            import traceback

            print("INFO: Loading Florence-2 caption model...")

            # Load processor from base Florence-2 model (matches handler.py)
            self.caption_processor = AutoProcessor.from_pretrained(
                "microsoft/Florence-2-base", trust_remote_code=True
            )

            # Load fine-tuned caption model from local weights
            # Only use float16 for GPU, no dtype specified for CPU (matches handler.py)
            if self.device.type == "cuda":
                self.caption_model = AutoModelForCausalLM.from_pretrained(
                    str(WEIGHTS_DIR / "icon_caption"),
                    torch_dtype=torch.float16,
                    trust_remote_code=True,
                ).to(self.device)
            else:
                self.caption_model = AutoModelForCausalLM.from_pretrained(
                    str(WEIGHTS_DIR / "icon_caption"),
                    trust_remote_code=True,
                ).to(self.device)

            print("INFO: Florence-2 loaded successfully.")
        except Exception as e:
            import traceback
            print(f"WARNING: Failed to load Florence-2: {e}")
            traceback.print_exc()
            print("INFO: Captioning will be disabled")
            self.caption_model = "disabled"
            self.caption_processor = None

    def load_ocr(self):
        if self.ocr is not None:
            return
        import easyocr

        print("INFO: Loading EasyOCR...")
        self.ocr = easyocr.Reader(["en"], gpu=(self.device.type == "cuda"))
        print("INFO: EasyOCR loaded.")

    def load_all(self):
        """Load all models."""
        self.load_yolo()
        self.load_ocr()
        self.load_caption()
        self.models_loaded = True


state = ModelState()


@app.on_event("startup")
async def startup_event():
    """Pre-load models on startup."""
    print(f"INFO: Starting OmniParser server on {str(state.device)}")
    try:
        state.load_all()
        print("INFO: All models loaded successfully")
    except Exception as e:
        print(f"WARNING: Failed to pre-load models: {e}")
        print("INFO: Models will be loaded on first request")


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        models_loaded=state.models_loaded,
        device=str(state.device),
    )


@app.get("/ready")
async def ready():
    """Readiness check - returns 200 only when models are loaded."""
    if not state.models_loaded:
        raise HTTPException(status_code=503, detail="Models not yet loaded")
    return {"status": "ready"}


def run_ocr(image: Image.Image) -> tuple[list[str], list[list[float]]]:
    """Run OCR, return (texts, bboxes) where bboxes are normalized xyxy."""
    state.load_ocr()
    w, h = image.size
    image_np = np.array(image.convert("RGB"))

    # EasyOCR returns list of (bbox, text, confidence)
    result = state.ocr.readtext(image_np)
    if not result:
        return [], []

    texts = []
    bboxes = []
    for detection in result:
        points, text, conf = detection
        if conf < 0.5:
            continue
        # points is [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
        x_coords = [p[0] for p in points]
        y_coords = [p[1] for p in points]
        bbox = [
            min(x_coords) / w,
            min(y_coords) / h,
            max(x_coords) / w,
            max(y_coords) / h,
        ]
        texts.append(text)
        bboxes.append(bbox)

    return texts, bboxes


def run_detection(image: Image.Image, threshold: float = 0.05) -> tuple[list[list[float]], list[float]]:
    """Run YOLO detection, return (bboxes, confidences) where bboxes are normalized xyxy."""
    state.load_yolo()
    w, h = image.size

    result = state.yolo_model.predict(source=image, conf=threshold, verbose=False)[0]
    if result.boxes is None:
        return [], []

    boxes = result.boxes.xyxy.cpu()
    confs = result.boxes.conf.cpu().tolist()

    bboxes = (boxes / torch.tensor([w, h, w, h])).tolist()
    return bboxes, confs


def run_captioning(images: list[Image.Image], batch_size: int = 64) -> list[str]:
    """Run captioning on cropped images (matches handler.py pattern)."""
    state.load_caption()
    if not images:
        return []

    # If captioning is disabled, return placeholder descriptions
    if state.caption_model == "disabled" or state.caption_processor is None:
        return ["icon" for _ in images]

    prompt = "<CAPTION>"
    captions = []

    for i in range(0, len(images), batch_size):
        batch = images[i : i + batch_size]
        inputs = state.caption_processor(
            images=batch,
            text=[prompt] * len(batch),
            return_tensors="pt",
            do_resize=False,
        )

        # Move to device with float16 for GPU (matches handler.py)
        if state.device.type == "cuda":
            inputs = inputs.to(device=state.device, dtype=torch.float16)
        else:
            inputs = inputs.to(device=state.device)

        with torch.inference_mode():
            generated_ids = state.caption_model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=20,
                num_beams=1,
                do_sample=False,
                early_stopping=False,
            )

        texts = state.caption_processor.batch_decode(generated_ids, skip_special_tokens=True)
        captions.extend([t.strip() for t in texts])

    return captions


def box_area(box: list[float]) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def iou(box1: list[float], box2: list[float]) -> float:
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = box_area(box1)
    area2 = box_area(box2)
    union = area1 + area2 - inter + 1e-6
    return max(inter / union, inter / (area1 + 1e-6), inter / (area2 + 1e-6))


def is_inside(inner: list[float], outer: list[float], threshold: float = 0.8) -> bool:
    x1 = max(inner[0], outer[0])
    y1 = max(inner[1], outer[1])
    x2 = min(inner[2], outer[2])
    y2 = min(inner[3], outer[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    inner_area = box_area(inner)
    return (inter / (inner_area + 1e-6)) > threshold


def draw_annotations(image: Image.Image, elements: list[dict], w: int, h: int) -> Image.Image:
    """Draw numbered bounding boxes."""
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    colors = [
        (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
        (255, 0, 255), (0, 255, 255), (128, 0, 0), (0, 128, 0),
        (0, 0, 128), (128, 128, 0), (128, 0, 128), (0, 128, 128),
    ]

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except (IOError, OSError):
        font = ImageFont.load_default()

    for el in elements:
        color = colors[el["id"] % len(colors)]
        x1, y1, x2, y2 = el["box_2d"]
        draw.rectangle([x1, y1, x2, y2], outline=color, width=2)

        label = str(el["id"])
        bbox = draw.textbbox((x1, y1), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.rectangle([x1, y1 - th - 4, x1 + tw + 4, y1], fill=color)
        draw.text((x1 + 2, y1 - th - 2), label, fill=(255, 255, 255), font=font)

    return image


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    file: UploadFile = File(...),
    box_threshold: float = 0.05,
    iou_threshold: float = 0.7,
):
    """Analyze a UI screenshot for elements."""
    start_time = time.time()

    # Load image
    contents = await file.read()
    image = Image.open(io.BytesIO(contents))
    if image.mode != "RGB":
        image = image.convert("RGB")

    w, h = image.size
    image_np = np.array(image)

    # Run OCR
    t0 = time.time()
    ocr_texts, ocr_bboxes = run_ocr(image)
    print(f"OCR: {len(ocr_texts)} text regions ({time.time() - t0:.2f}s)")

    # Run detection
    t0 = time.time()
    det_bboxes, det_confs = run_detection(image, box_threshold)
    print(f"Detection: {len(det_bboxes)} icons ({time.time() - t0:.2f}s)")

    # Build element list
    elements_data = []

    # Add OCR elements
    for text, bbox in zip(ocr_texts, ocr_bboxes):
        elements_data.append({
            "type": "text",
            "bbox": bbox,
            "text": text,
            "description": None,
            "confidence": 1.0,
        })

    # Add detection elements
    for bbox, conf in zip(det_bboxes, det_confs):
        skip = False
        for other_bbox, other_conf in zip(det_bboxes, det_confs):
            if bbox == other_bbox:
                continue
            if iou(bbox, other_bbox) > iou_threshold:
                if box_area(bbox) > box_area(other_bbox):
                    skip = True
                    break
        if skip:
            continue

        contained_text = []
        for text, ocr_bbox in zip(ocr_texts, ocr_bboxes):
            if is_inside(ocr_bbox, bbox):
                contained_text.append(text)

        elements_data.append({
            "type": "icon",
            "bbox": bbox,
            "text": " ".join(contained_text) if contained_text else None,
            "description": None,
            "confidence": conf,
        })

    # Caption icons without text
    t0 = time.time()
    icons_to_caption = [e for e in elements_data if e["type"] == "icon" and not e["text"]]
    if icons_to_caption:
        cropped_images = []
        for el in icons_to_caption:
            x1, y1, x2, y2 = el["bbox"]
            x1p, y1p = int(x1 * w), int(y1 * h)
            x2p, y2p = int(x2 * w), int(y2 * h)
            cropped = image_np[y1p:y2p, x1p:x2p]
            if cropped.size == 0:
                cropped_images.append(Image.new("RGB", (64, 64)))
            else:
                cropped = cv2.resize(cropped, (64, 64))
                cropped_images.append(ToPILImage()(cropped))

        captions = run_captioning(cropped_images)
        for el, caption in zip(icons_to_caption, captions):
            el["description"] = caption

    print(f"Captioning: {len(icons_to_caption)} icons ({time.time() - t0:.2f}s)")

    # Build final element list with IDs
    elements = []
    for i, el in enumerate(elements_data):
        x1, y1, x2, y2 = el["bbox"]
        box_pixels = [int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)]
        center_x = (box_pixels[0] + box_pixels[2]) // 2
        center_y = (box_pixels[1] + box_pixels[3]) // 2

        elements.append({
            "id": i,
            "box_2d": box_pixels,
            "box_2d_normalized": el["bbox"],
            "type": el["type"],
            "text": el["text"],
            "description": el["description"],
            "confidence": el["confidence"],
            "center_pixel": [center_x, center_y],
            "center_normalized": [(x1 + x2) / 2, (y1 + y2) / 2],
        })

    # Draw annotations
    annotated = draw_annotations(image.copy(), elements, w, h)

    # Encode annotated image as base64
    buffer = io.BytesIO()
    annotated.save(buffer, format="PNG")
    import base64
    annotated_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    processing_time = int((time.time() - start_time) * 1000)

    return AnalyzeResponse(
        success=True,
        image_width=w,
        image_height=h,
        element_count=len(elements),
        elements=elements,
        annotated_image_base64=annotated_b64,
        processing_time_ms=processing_time,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
