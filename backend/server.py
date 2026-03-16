from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any
import uuid
from datetime import datetime, timezone
import asyncio
import base64
import io
import json
import re
import zipfile

import pandas as pd
import requests
from emergentintegrations.llm.chat import LlmChat, UserMessage
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')
GENERATED_PINS_DIR = ROOT_DIR / "generated_pins"
GENERATED_PINS_DIR.mkdir(parents=True, exist_ok=True)

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()
app.mount("/api/static/pins", StaticFiles(directory=str(GENERATED_PINS_DIR)), name="pin-static")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# Define Models
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Ignore MongoDB's _id field
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StatusCheckCreate(BaseModel):
    client_name: str


class PinRecord(BaseModel):
    pin_id: str
    session_id: str
    quote: str
    meta_title: str
    meta_description: str
    hashtags: str
    tag_topic: str
    creator: str
    timing_link: str
    ai_prompt: str
    filename: str
    image_url: str
    created_at: str


class GeneratePinsResponse(BaseModel):
    session_id: str
    total_generated: int
    pins: List[PinRecord]


class GenerationSummary(BaseModel):
    generated_count: int
    completed: bool


REQUIRED_COLUMNS = [
    "Quote",
    "Meta Title",
    "Meta Description",
    "Hashtags",
    "TAG TOPIC",
    "CREATOR",
    "Timing Link",
]

HEADER_ALIASES: Dict[str, List[str]] = {
    "Quote": ["quote", "quotes", "pinquote", "quotation"],
    "Meta Title": ["metatitle", "title", "pintitle"],
    "Meta Description": ["metadescription", "metadesc", "description", "pindescription"],
    "Hashtags": ["hashtags", "hashtag", "tags"],
    "TAG TOPIC": ["tagtopic", "topic", "tag", "boardtopic"],
    "CREATOR": ["creator", "author", "owner"],
    "Timing Link": ["timinglink", "link", "url", "destinationlink", "timing"],
}

MOCK_IMAGE_URLS = [
    "https://static.prod-images.emergentagent.com/jobs/bf3547ed-e1b7-4e48-9bc6-e1aa83f707d1/images/328f52aa34fc8ae7916966a2d8faab8917150a4703a8c1f48bf8160a77a47d7b.png",
    "https://static.prod-images.emergentagent.com/jobs/bf3547ed-e1b7-4e48-9bc6-e1aa83f707d1/images/65f99e0695c649136b49c9330d20107f409043b4a9c73ede513a95cd50db4d51.png",
    "https://static.prod-images.emergentagent.com/jobs/bf3547ed-e1b7-4e48-9bc6-e1aa83f707d1/images/78b6c57beda2383b84eb8a1aeb417b512678dde21a228d423cdb33dc6268a902.png",
    "https://static.prod-images.emergentagent.com/jobs/bf3547ed-e1b7-4e48-9bc6-e1aa83f707d1/images/426acf8cd530ec7a944d38ff5a60f405e5214cfdf821cc7f02a53edaef6881ea.png",
]

GENERATION_TRACKER: Dict[str, Dict[str, Any]] = {}


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def slugify_filename(text: str, index: int) -> str:
    raw = clean_text(text).lower()
    sanitized = re.sub(r"[^a-z0-9\s-]", "", raw)
    sanitized = re.sub(r"\s+", "-", sanitized)
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    return sanitized[:90] if sanitized else f"pin-{index + 1}"


def build_ai_prompt(meta_title: str, meta_description: str, tag_topic: str) -> str:
    title = clean_text(meta_title)
    description = clean_text(meta_description)
    topic = clean_text(tag_topic) or "lifestyle"
    return (
        f"photorealistic Pinterest-style scene about {topic}, inspired by '{title}', "
        f"{description}, soft natural light, modern editorial composition, "
        "clean depth of field, emotionally engaging visual storytelling, no text"
    )


def create_placeholder_background() -> Image.Image:
    image = Image.new("RGB", (1000, 1500), (230, 230, 235))
    draw = ImageDraw.Draw(image)
    draw.rectangle([(0, 0), (1000, 450)], fill=(210, 220, 235))
    draw.rectangle([(0, 450), (1000, 950)], fill=(180, 195, 215))
    draw.rectangle([(0, 950), (1000, 1500)], fill=(145, 160, 185))
    return image


def load_backgrounds() -> List[Image.Image]:
    backgrounds: List[Image.Image] = []
    for url in MOCK_IMAGE_URLS:
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content)).convert("RGB")
            backgrounds.append(image)
        except Exception:
            logger.warning("Failed to fetch mock image: %s", url)
    if not backgrounds:
        backgrounds.append(create_placeholder_background())
    return backgrounds


async def generate_gemini_background(prompt: str, api_key: str, session_id: str, index: int) -> Image.Image:
    chat = LlmChat(
        api_key=api_key,
        session_id=f"{session_id}-bg-{index}-{uuid.uuid4()}",
        system_message=(
            "You generate photorealistic Pinterest-style vertical background images. "
            "Never include text, logos, or watermarks in the image."
        ),
    )
    chat.with_model("gemini", "gemini-3-pro-image-preview").with_params(modalities=["image", "text"])

    message = UserMessage(
        text=(
            "Create a high-quality photorealistic Pinterest background image in vertical composition (2:3 ratio). "
            "Modern clean aesthetic, soft cinematic lighting, strong subject clarity, no text in image. "
            f"Context: {prompt}"
        )
    )

    _, images = await asyncio.wait_for(chat.send_message_multimodal_response(message), timeout=90)
    if not images:
        raise RuntimeError("Gemini returned no image")

    image_base64 = images[0].get("data", "")
    if not image_base64:
        raise RuntimeError("Gemini returned empty image data")

    decoded = base64.b64decode(image_base64)
    return Image.open(io.BytesIO(decoded)).convert("RGB")


async def build_ai_background_pool(records: List[Dict[str, Any]], session_id: str) -> List[Image.Image]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return []

    unique_prompts: List[str] = []
    for row in records:
        prompt = build_ai_prompt(
            row.get("Meta Title", ""),
            row.get("Meta Description", ""),
            row.get("TAG TOPIC", ""),
        )
        if prompt not in unique_prompts:
            unique_prompts.append(prompt)

    pool_size = min(5, max(2, len(records) // 20 if len(records) >= 20 else 2), len(unique_prompts))
    selected_prompts = unique_prompts[:pool_size]
    if not selected_prompts:
        return []

    semaphore = asyncio.Semaphore(2)

    async def worker(idx: int, prompt: str) -> Image.Image | None:
        async with semaphore:
            try:
                return await generate_gemini_background(prompt, api_key, session_id, idx)
            except Exception as exc:
                logger.warning("Gemini background generation failed at index %s: %s", idx, exc)
                return None

    generated = await asyncio.gather(*(worker(idx, prompt) for idx, prompt in enumerate(selected_prompts)))
    return [image for image in generated if image is not None]


def normalize_header_name(value: Any) -> str:
    lowered = clean_text(value).lower()
    return re.sub(r"[^a-z0-9]", "", lowered)


def read_tabular_file(file_name: str, file_bytes: bytes, header: int | None) -> pd.DataFrame:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".csv":
        try:
            dataframe = pd.read_csv(io.BytesIO(file_bytes), header=header, dtype=str)
        except UnicodeDecodeError:
            dataframe = pd.read_csv(io.BytesIO(file_bytes), header=header, dtype=str, encoding="latin-1")
    elif suffix == ".xlsx":
        dataframe = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl", header=header, dtype=str)
    else:
        raise HTTPException(status_code=400, detail="Upload must be .xlsx or .csv")

    return dataframe


def detect_header_row(raw_dataframe: pd.DataFrame) -> int | None:
    scan_limit = min(len(raw_dataframe), 12)
    required_markers = {"quote", "metatitle", "metadescription", "hashtags"}

    for row_index in range(scan_limit):
        row_values = {
            normalize_header_name(value)
            for value in raw_dataframe.iloc[row_index].tolist()
            if clean_text(value)
        }
        if required_markers.issubset(row_values):
            return row_index

    return None


def standardize_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    normalized_columns = {
        normalize_header_name(column): str(column)
        for column in dataframe.columns
        if clean_text(column)
    }

    def resolve_column(canonical_name: str) -> str | None:
        aliases = HEADER_ALIASES[canonical_name]
        for alias in aliases:
            if alias in normalized_columns:
                return normalized_columns[alias]
        return None

    resolved_columns: Dict[str, str] = {}
    missing: List[str] = []
    for canonical in REQUIRED_COLUMNS:
        column_name = resolve_column(canonical)
        if column_name is None:
            missing.append(canonical)
        else:
            resolved_columns[canonical] = column_name

    if missing:
        available_headers = [clean_text(column) for column in dataframe.columns if clean_text(column)]
        raise HTTPException(
            status_code=400,
            detail=(
                f"Missing columns: {', '.join(missing)}. "
                f"Detected headers: {', '.join(available_headers) if available_headers else 'none'}"
            ),
        )

    parsed = pd.DataFrame()

    for canonical in REQUIRED_COLUMNS:
        source_column = resolved_columns[canonical]
        parsed[canonical] = dataframe[source_column].fillna("").astype(str).map(clean_text)

    topic_column = resolved_columns.get("TAG TOPIC")
    tag_column = normalized_columns.get("tag")
    if topic_column and tag_column and topic_column != tag_column:
        topic_series = dataframe[topic_column].fillna("").astype(str).map(clean_text)
        tag_series = dataframe[tag_column].fillna("").astype(str).map(clean_text)
        parsed["TAG TOPIC"] = topic_series.where(topic_series != "", tag_series)

    link_column = resolved_columns.get("Timing Link")
    timing_column = normalized_columns.get("timing")
    if link_column and timing_column and link_column != timing_column:
        link_series = dataframe[link_column].fillna("").astype(str).map(clean_text)
        timing_series = dataframe[timing_column].fillna("").astype(str).map(clean_text)
        parsed["Timing Link"] = link_series.where(link_series != "", timing_series)

    parsed = parsed.replace("nan", "")
    parsed = parsed[(parsed["Quote"] != "") & (parsed["Meta Title"] != "")].reset_index(drop=True)

    if parsed.empty:
        raise HTTPException(status_code=400, detail="No valid rows found after parsing. Please verify your header row.")

    return parsed


def parse_file(file_name: str, file_bytes: bytes) -> pd.DataFrame:
    initial = read_tabular_file(file_name, file_bytes, header=0)
    try:
        return standardize_dataframe(initial)
    except HTTPException as first_error:
        raw = read_tabular_file(file_name, file_bytes, header=None)
        header_row = detect_header_row(raw)
        if header_row is None:
            raise first_error

        header_values = [clean_text(value) for value in raw.iloc[header_row].tolist()]
        rebuilt = raw.iloc[header_row + 1 :].copy().reset_index(drop=True)
        rebuilt.columns = header_values
        return standardize_dataframe(rebuilt)


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for candidate in font_candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    words = text.split()
    if not words:
        return ["Your quote here"]

    lines: List[str] = []
    current_line: List[str] = []

    for word in words:
        test_line = " ".join(current_line + [word])
        text_box = draw.textbbox((0, 0), test_line, font=font)
        text_width = text_box[2] - text_box[0]

        if text_width <= max_width or not current_line:
            current_line.append(word)
        else:
            lines.append(" ".join(current_line))
            current_line = [word]

    if current_line:
        lines.append(" ".join(current_line))

    return lines


def render_pin(background: Image.Image, quote: str, text_position: str) -> Image.Image:
    canvas = ImageOps.fit(background, (1000, 1500), method=Image.Resampling.LANCZOS).convert("RGBA")
    overlay = Image.new("RGBA", (1000, 1500), (0, 0, 0, 45))
    canvas = Image.alpha_composite(canvas, overlay)

    draw = ImageDraw.Draw(canvas)
    bar_height = 180
    draw.rectangle([(0, 1500 - bar_height), (1000, 1500)], fill=(255, 255, 255, 248))

    quote_text = clean_text(quote) or "Create your momentum one step at a time"
    quote_area_width = 860

    selected_font = get_font(82, bold=True)
    lines = wrap_text(draw, quote_text, selected_font, quote_area_width)
    line_height = 92
    while (len(lines) > 6 or max(len(line) for line in lines) > 55) and line_height > 56:
        line_height -= 8
        selected_font = get_font(line_height, bold=True)
        lines = wrap_text(draw, quote_text, selected_font, quote_area_width)

    total_text_height = len(lines) * (line_height + 12)
    center_y_map = {
        "top": 430,
        "center": 700,
        "bottom": 920,
    }
    center_y = center_y_map.get(text_position, 700)

    start_y = int(center_y - (total_text_height / 2))
    start_y = max(110, min(start_y, 1500 - bar_height - total_text_height - 50))

    for index, line in enumerate(lines):
        line_box = draw.textbbox((0, 0), line, font=selected_font)
        line_width = line_box[2] - line_box[0]
        x = int((1000 - line_width) / 2)
        y = start_y + index * (line_height + 12)
        draw.text((x + 2, y + 2), line, font=selected_font, fill=(0, 0, 0, 145))
        draw.text((x, y), line, font=selected_font, fill=(255, 255, 255, 252))

    cta_font = get_font(44, bold=True)
    cta_text = "Tap to learn more"
    cta_box = draw.textbbox((0, 0), cta_text, font=cta_font)
    cta_width = cta_box[2] - cta_box[0]
    cta_height = cta_box[3] - cta_box[1]
    draw.text(
        ((1000 - cta_width) / 2, 1500 - bar_height + (bar_height - cta_height) / 2 - 6),
        cta_text,
        font=cta_font,
        fill=(31, 41, 55),
    )

    return canvas.convert("RGB")


def make_unique_filename(session_dir: Path, base_slug: str) -> str:
    candidate = f"{base_slug}.png"
    counter = 2
    while (session_dir / candidate).exists():
        candidate = f"{base_slug}-{counter}.png"
        counter += 1
    return candidate


def create_pin_payload(
    row: Dict[str, Any],
    index: int,
    session_id: str,
    session_dir: Path,
    text_position: str,
    template_image: Image.Image | None,
    backgrounds: List[Image.Image],
) -> Dict[str, Any]:
    quote = clean_text(row.get("Quote"))
    prompt = build_ai_prompt(
        row.get("Meta Title", ""),
        row.get("Meta Description", ""),
        row.get("TAG TOPIC", ""),
    )

    if template_image is not None:
        base_image = template_image.copy()
    else:
        base_image = backgrounds[index % len(backgrounds)].copy()

    rendered = render_pin(base_image, quote, text_position)
    base_slug = slugify_filename(quote, index)
    file_name = make_unique_filename(session_dir, base_slug)
    file_path = session_dir / file_name
    rendered.save(file_path, format="PNG")

    return {
        "pin_id": str(uuid.uuid4()),
        "session_id": session_id,
        "quote": quote,
        "meta_title": clean_text(row.get("Meta Title")),
        "meta_description": clean_text(row.get("Meta Description")),
        "hashtags": clean_text(row.get("Hashtags")),
        "tag_topic": clean_text(row.get("TAG TOPIC")),
        "creator": clean_text(row.get("CREATOR")),
        "timing_link": clean_text(row.get("Timing Link")),
        "ai_prompt": prompt,
        "filename": file_name,
        "image_url": f"/api/static/pins/{session_id}/{file_name}",
        "file_path": str(file_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def to_public_pin(document: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if key != "file_path"
    }

# Add your routes to the router instead of directly to app
@api_router.get("/")
async def root():
    return {"message": "Hello World"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)
    
    # Convert to dict and serialize datetime to ISO string for MongoDB
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    
    _ = await db.status_checks.insert_one(doc)
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    # Exclude MongoDB's _id field from the query results
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    
    # Convert ISO string timestamps back to datetime objects
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    
    return status_checks


@api_router.post("/pins/generate", response_model=GeneratePinsResponse)
async def generate_pins(
    data_file: UploadFile = File(...),
    template_image: UploadFile | None = File(default=None),
    template_text_position: str = Form("center"),
    max_pins: int = Form(50),
):
    if max_pins < 1:
        raise HTTPException(status_code=400, detail="max_pins must be at least 1")

    if template_text_position not in {"top", "center", "bottom"}:
        raise HTTPException(status_code=400, detail="template_text_position must be top, center, or bottom")

    total_limit = min(max_pins, 500)
    file_bytes = await data_file.read()
    dataframe = parse_file(data_file.filename or "", file_bytes)

    if dataframe.empty:
        raise HTTPException(status_code=400, detail="Uploaded file has no rows")

    dataframe = dataframe.head(total_limit)
    records = dataframe.to_dict(orient="records")

    session_id = str(uuid.uuid4())
    session_dir = GENERATED_PINS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    template_obj = None
    if template_image is not None and template_image.filename:
        template_bytes = await template_image.read()
        try:
            template_obj = Image.open(io.BytesIO(template_bytes)).convert("RGB")
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid template image") from exc

    if template_obj is None:
        backgrounds = await build_ai_background_pool(records, session_id)
        if not backgrounds:
            backgrounds = await asyncio.to_thread(load_backgrounds)
    else:
        backgrounds = []
    generation_progress = {
        "generated_count": 0,
        "total_count": len(records),
        "completed": False,
    }
    GENERATION_TRACKER[session_id] = generation_progress
    progress_lock = asyncio.Lock()

    semaphore = asyncio.Semaphore(12)

    async def process_row(index: int, row: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            pin = await asyncio.to_thread(
                create_pin_payload,
                row,
                index,
                session_id,
                session_dir,
                template_text_position,
                template_obj,
                backgrounds,
            )
            async with progress_lock:
                generation_progress["generated_count"] += 1
            return pin

    tasks = [process_row(index, row) for index, row in enumerate(records)]
    generated = await asyncio.gather(*tasks)
    generation_progress["completed"] = True

    await db.pin_records.insert_many([dict(pin) for pin in generated])
    await db.generation_sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "session_id": session_id,
                "generated_count": len(generated),
                "completed": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
        upsert=True,
    )
    GENERATION_TRACKER.pop(session_id, None)

    return {
        "session_id": session_id,
        "total_generated": len(generated),
        "pins": [to_public_pin(pin) for pin in generated],
    }


@api_router.get("/pins/{session_id}", response_model=List[PinRecord])
async def get_generated_pins(session_id: str):
    pins = await db.pin_records.find(
        {"session_id": session_id}, {"_id": 0, "file_path": 0}
    ).to_list(length=2000)
    return pins


@api_router.get("/pins/progress/{session_id}", response_model=GenerationSummary)
async def get_generation_progress(session_id: str):
    live_progress = GENERATION_TRACKER.get(session_id)
    if live_progress:
        return {
            "generated_count": int(live_progress.get("generated_count", 0)),
            "completed": bool(live_progress.get("completed", False)),
        }

    session = await db.generation_sessions.find_one({"session_id": session_id}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "generated_count": int(session.get("generated_count", 0)),
        "completed": bool(session.get("completed", False)),
    }


@api_router.get("/pins/download/{pin_id}")
async def download_pin(pin_id: str):
    document = await db.pin_records.find_one({"pin_id": pin_id}, {"_id": 0})
    if not document:
        raise HTTPException(status_code=404, detail="Pin not found")

    file_path = Path(document["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Pin file missing")

    return FileResponse(path=file_path, media_type="image/png", filename=document["filename"])


@api_router.get("/pins/download-all/{session_id}")
async def download_all_pins(session_id: str):
    documents = await db.pin_records.find({"session_id": session_id}, {"_id": 0}).to_list(length=2000)
    if not documents:
        raise HTTPException(status_code=404, detail="No pins found for this session")

    zip_path = GENERATED_PINS_DIR / session_id / f"{session_id}-pins.zip"
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for document in documents:
            file_path = Path(document["file_path"])
            if file_path.exists():
                archive.write(file_path, arcname=document["filename"])

    return FileResponse(path=zip_path, media_type="application/zip", filename=f"{session_id}-pins.zip")


@api_router.get("/pins/export/{session_id}")
async def export_metadata(session_id: str, export_format: str = "csv"):
    documents = await db.pin_records.find(
        {"session_id": session_id},
        {"_id": 0, "file_path": 0},
    ).to_list(length=2000)

    if not documents:
        raise HTTPException(status_code=404, detail="No metadata found for this session")

    export_format = export_format.lower()
    target_dir = GENERATED_PINS_DIR / session_id
    target_dir.mkdir(parents=True, exist_ok=True)

    if export_format == "json":
        output_path = target_dir / f"{session_id}-metadata.json"
        with output_path.open("w", encoding="utf-8") as json_file:
            json.dump(documents, json_file, ensure_ascii=False, indent=2)
        media_type = "application/json"
    else:
        output_path = target_dir / f"{session_id}-metadata.csv"
        dataframe = pd.DataFrame(documents)
        dataframe.to_csv(output_path, index=False)
        media_type = "text/csv"

    return FileResponse(path=output_path, media_type=media_type, filename=output_path.name)

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()