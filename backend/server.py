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
from typing import List, Dict, Any, Optional
from collections import deque
import uuid
from datetime import datetime, timezone
import asyncio
import base64
import io
import json
import re
import shutil
import zipfile

import pandas as pd
import requests
from emergentintegrations.llm.chat import LlmChat, UserMessage
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
from docx import Document


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')
GENERATED_PINS_DIR = ROOT_DIR / "generated_pins"
GENERATED_PINS_DIR.mkdir(parents=True, exist_ok=True)

# MongoDB optional — omit MONGO_URL for stateless deploys (memory-only sessions; downloads work until evicted or restart).
mongo_url = os.environ.get("MONGO_URL", "").strip()
client: Optional[AsyncIOMotorClient] = None
db: Optional[Any] = None
if mongo_url:
    client = AsyncIOMotorClient(mongo_url)
    db_name = os.environ.get("DB_NAME", "pinterest_pins").strip() or "pinterest_pins"
    db = client[db_name]

MAX_EPHEMERAL_SESSIONS = max(1, int(os.environ.get("MAX_EPHEMERAL_SESSIONS", "40")))
_EPHEMERAL_LOCK = asyncio.Lock()
_EPHEMERAL_SESSION_ORDER: deque[str] = deque()
_EPHEMERAL_PINS_BY_SESSION: Dict[str, List[Dict[str, Any]]] = {}
_EPHEMERAL_PIN_BY_ID: Dict[str, Dict[str, Any]] = {}


async def _evict_oldest_ephemeral_sessions() -> None:
    while len(_EPHEMERAL_SESSION_ORDER) > MAX_EPHEMERAL_SESSIONS:
        old_sid = _EPHEMERAL_SESSION_ORDER.popleft()
        old_pins = _EPHEMERAL_PINS_BY_SESSION.pop(old_sid, [])
        for pin in old_pins:
            _EPHEMERAL_PIN_BY_ID.pop(pin.get("pin_id"), None)
        old_dir = GENERATED_PINS_DIR / old_sid
        if old_dir.is_dir():
            await asyncio.to_thread(lambda: shutil.rmtree(old_dir, ignore_errors=True))


async def _remember_ephemeral_session(session_id: str, pins: List[Dict[str, Any]]) -> None:
    async with _EPHEMERAL_LOCK:
        _EPHEMERAL_SESSION_ORDER.append(session_id)
        _EPHEMERAL_PINS_BY_SESSION[session_id] = pins
        for pin in pins:
            _EPHEMERAL_PIN_BY_ID[pin["pin_id"]] = pin
        await _evict_oldest_ephemeral_sessions()

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
    pin_name: str = ""
    pic_no: str = ""
    quote: str
    pinrest_input: str = ""
    meta_title: str
    meta_description: str
    hashtags: str
    tag_topic: str
    creator: str
    timing_link: str
    ai_prompt: str
    mode: str = "ai"
    filename: str
    image_url: str
    created_at: str


class GeneratePinsResponse(BaseModel):
    session_id: str
    total_generated: int
    skipped_rows: int = 0
    warnings: List[str] = []
    mode_used: str = "ai"
    auto_switched: bool = False
    switch_message: str = ""
    total_rows_processed: int = 0
    images_matched: int = 0
    missing_images_count: int = 0
    pins: List[PinRecord]


class GenerationSummary(BaseModel):
    generated_count: int
    completed: bool


REQUIRED_COLUMNS = [
    "PIC NO.",
    "PIN NAME",
    "Quote",
    "PINREST INPUT",
    "Meta Title",
    "Meta Description",
    "Hashtags",
    "TAG TOPIC",
    "CREATOR",
    "Timing Link",
]

HEADER_ALIASES: Dict[str, List[str]] = {
    "PIC NO.": ["picno", "picno.", "picnumber", "pic"],
    "PIN NAME": ["pinname"],
    "Quote": ["quote", "quotes", "pinquote", "quotation", "pintitle1stlinebold"],
    "PINREST INPUT": ["pinrestinput"],
    "Meta Title": ["metatitle", "title", "pintitle", "pinrestinput", "pintitle1stlinebold"],
    "Meta Description": ["metadescription", "metadesc", "description", "pindescription", "pindescription1", "pindescription2"],
    "Hashtags": ["hashtags", "hashtag", "tags"],
    "TAG TOPIC": ["tagtopic", "topic", "tag", "boardtopic", "pintitle2ndline"],
    "CREATOR": ["creator", "author", "owner"],
    "Timing Link": ["timinglink", "link", "url", "destinationlink", "timing", "links"],
}

MANDATORY_COLUMNS = {"PIN NAME", "Quote"}

MOCK_IMAGE_URLS = [
    "https://static.prod-images.emergentagent.com/jobs/bf3547ed-e1b7-4e48-9bc6-e1aa83f707d1/images/328f52aa34fc8ae7916966a2d8faab8917150a4703a8c1f48bf8160a77a47d7b.png",
    "https://static.prod-images.emergentagent.com/jobs/bf3547ed-e1b7-4e48-9bc6-e1aa83f707d1/images/65f99e0695c649136b49c9330d20107f409043b4a9c73ede513a95cd50db4d51.png",
    "https://static.prod-images.emergentagent.com/jobs/bf3547ed-e1b7-4e48-9bc6-e1aa83f707d1/images/78b6c57beda2383b84eb8a1aeb417b512678dde21a228d423cdb33dc6268a902.png",
    "https://static.prod-images.emergentagent.com/jobs/bf3547ed-e1b7-4e48-9bc6-e1aa83f707d1/images/426acf8cd530ec7a944d38ff5a60f405e5214cfdf821cc7f02a53edaef6881ea.png",
]

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

GENERATION_TRACKER: Dict[str, Dict[str, Any]] = {}


class AIQuotaExceededError(Exception):
    pass


class AIImageGenerationError(Exception):
    pass


def is_quota_error_message(message: str) -> bool:
    lowered = clean_text(message).lower()
    markers = [
        "quota",
        "resource_exhausted",
        "resourceexhausted",
        "429",
        "rate limit",
        "too many requests",
        "insufficient quota",
    ]
    return any(marker in lowered for marker in markers)


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_pic_no(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return ""

    sanitized = raw.replace(",", "").strip()
    if re.fullmatch(r"\d+", sanitized):
        return str(int(sanitized))

    if re.fullmatch(r"\d+\.0+", sanitized):
        return str(int(float(sanitized)))

    return ""


def is_supported_image_name(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def extract_zip_image_entries(zip_bytes: bytes) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as archive:
            for item in archive.infolist():
                if item.is_dir():
                    continue
                base_name = Path(item.filename).name
                if not base_name or not is_supported_image_name(base_name):
                    continue
                with archive.open(item) as file_obj:
                    entries.append({"filename": base_name, "content": file_obj.read()})
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid ZIP file for custom images") from exc

    return entries


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
        f"photorealistic Pinterest aesthetic scene about {topic}, inspired by '{title}', "
        f"{description}, soft lighting, lifestyle photography, vertical composition, high detail, "
        "unique framing and camera angle, emotionally engaging visual storytelling, no text"
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


async def generate_gemini_background(
    prompt: str,
    api_key: str,
    session_id: str,
    index: int,
    timeout_seconds: int = 90,
) -> Image.Image:
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

    try:
        _, images = await asyncio.wait_for(chat.send_message_multimodal_response(message), timeout=timeout_seconds)
    except Exception as exc:
        raw_message = str(exc)
        if is_quota_error_message(raw_message):
            raise AIQuotaExceededError(raw_message) from exc
        raise AIImageGenerationError(raw_message) from exc

    if not images:
        raise AIImageGenerationError("Gemini returned no image")

    image_base64 = images[0].get("data", "")
    if not image_base64:
        raise AIImageGenerationError("Gemini returned empty image data")

    decoded = base64.b64decode(image_base64)
    return Image.open(io.BytesIO(decoded)).convert("RGB")


async def build_ai_background_pool(records: List[Dict[str, Any]], session_id: str) -> List[Image.Image]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise AIImageGenerationError("Gemini API key missing")

    unique_prompts: List[str] = []
    for row in records:
        prompt = build_ai_prompt(
            row.get("Meta Title", ""),
            row.get("Meta Description", ""),
            row.get("TAG TOPIC", ""),
        )
        if prompt not in unique_prompts:
            unique_prompts.append(prompt)

    pool_size = min(12, max(3, len(records) // 10 if len(records) >= 20 else 3), len(unique_prompts))
    selected_prompts = unique_prompts[:pool_size]
    if not selected_prompts:
        raise AIImageGenerationError("No prompts available for image generation")

    preflight_image = await generate_gemini_background(
        selected_prompts[0],
        api_key,
        session_id,
        0,
        timeout_seconds=25,
    )

    semaphore = asyncio.Semaphore(2)

    async def worker(idx: int, prompt: str) -> Image.Image | None:
        async with semaphore:
            try:
                return await generate_gemini_background(prompt, api_key, session_id, idx)
            except AIQuotaExceededError:
                raise
            except Exception as exc:
                logger.warning("Gemini background generation failed at index %s: %s", idx, exc)
                return None

    generated: List[Image.Image | None] = [preflight_image]
    remaining_prompts = selected_prompts[1:]
    if remaining_prompts:
        generated.extend(
            await asyncio.gather(*(worker(idx + 1, prompt) for idx, prompt in enumerate(remaining_prompts)))
        )

    usable = [image for image in generated if image is not None]
    if not usable:
        raise AIImageGenerationError("Gemini did not return usable images")
    return usable


def apply_background_variation(base_image: Image.Image, index: int) -> Image.Image:
    varied = base_image.copy().convert("RGB")
    width, height = varied.size

    zoom_factor = 1.04 + ((index % 5) * 0.01)
    crop_width = int(width / zoom_factor)
    crop_height = int(height / zoom_factor)
    x_shift = (index * 13) % max(1, width - crop_width + 1)
    y_shift = (index * 17) % max(1, height - crop_height + 1)
    varied = varied.crop((x_shift, y_shift, x_shift + crop_width, y_shift + crop_height)).resize((width, height), Image.Resampling.LANCZOS)

    brightness = 0.93 + ((index % 7) * 0.02)
    contrast = 0.92 + ((index % 6) * 0.03)
    color = 0.95 + ((index % 5) * 0.02)

    varied = ImageEnhance.Brightness(varied).enhance(brightness)
    varied = ImageEnhance.Contrast(varied).enhance(contrast)
    varied = ImageEnhance.Color(varied).enhance(color)
    return varied


def parse_docx_quotes(file_bytes: bytes) -> List[str]:
    try:
        document = Document(io.BytesIO(file_bytes))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Word file. Please upload a .docx file.") from exc

    quotes = [clean_text(paragraph.text) for paragraph in document.paragraphs if clean_text(paragraph.text)]
    if not quotes:
        raise HTTPException(status_code=400, detail="No usable quote text found in Word file.")
    return quotes


def parse_image_links(image_links_raw: str) -> List[str]:
    if not clean_text(image_links_raw):
        return []
    chunks = re.split(r"[\n,]", image_links_raw)
    return [clean_text(chunk) for chunk in chunks if clean_text(chunk).startswith("http")]


def load_custom_image_assets(
    file_entries: List[Dict[str, Any]],
    image_links_raw: str,
) -> List[Dict[str, Any]]:
    assets: List[Dict[str, Any]] = []

    for entry in file_entries:
        filename = clean_text(entry.get("filename"))
        content = entry.get("content", b"")
        if not filename or not content or not is_supported_image_name(filename):
            continue
        try:
            image = Image.open(io.BytesIO(content)).convert("RGB")
            stem = Path(filename).stem
            assets.append(
                {
                    "slug": slugify_filename(stem, 0),
                    "pic_no": normalize_pic_no(stem),
                    "image": image,
                }
            )
        except Exception:
            logger.warning("Skipping invalid custom image file: %s", filename)

    for url in parse_image_links(image_links_raw):
        try:
            file_name = Path(url.split("?")[0]).name
            if not is_supported_image_name(file_name):
                continue
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content)).convert("RGB")
            stem = Path(url.split("?")[0]).stem or "link-image"
            assets.append(
                {
                    "slug": slugify_filename(stem, 0),
                    "pic_no": normalize_pic_no(stem),
                    "image": image,
                }
            )
        except Exception:
            logger.warning("Skipping invalid custom image URL: %s", url)

    return assets


async def build_ai_row_backgrounds(records: List[Dict[str, Any]], session_id: str) -> List[Image.Image]:
    ai_pool = await build_ai_background_pool(records, session_id)

    return [apply_background_variation(ai_pool[index % len(ai_pool)], index) for index in range(len(records))]


def build_custom_row_backgrounds(
    records: List[Dict[str, Any]],
    assets: List[Dict[str, Any]],
    mapping_strategy: str,
) -> Dict[str, Any]:
    if not assets:
        raise HTTPException(status_code=400, detail="Custom mode requires uploaded images or image links.")

    pic_map: Dict[str, Image.Image] = {}
    for asset in assets:
        pic_no_key = clean_text(asset.get("pic_no"))
        if pic_no_key and pic_no_key not in pic_map:
            pic_map[pic_no_key] = asset["image"]

    row_backgrounds: List[Image.Image] = []
    matched_records: List[Dict[str, Any]] = []
    missing_warnings: List[str] = []
    missing_count = 0
    matched_count = 0

    for index, row in enumerate(records, start=1):
        pic_no_key = normalize_pic_no(row.get("PIC NO."))
        base_image = pic_map.get(pic_no_key) if pic_no_key else None

        if base_image is None:
            missing_count += 1
            missing_warnings.append(f"Image not found for PIC NO. {pic_no_key or 'N/A'} (row {index}).")
            continue

        matched_records.append(row)
        matched_count += 1
        row_backgrounds.append(apply_background_variation(base_image, index - 1))

    if not matched_records:
        raise HTTPException(status_code=400, detail="No custom images matched the provided PIC NO. values.")

    return {
        "records": matched_records,
        "backgrounds": row_backgrounds,
        "matched_count": matched_count,
        "missing_count": missing_count,
        "warnings": missing_warnings,
    }


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


def is_two_section_pin_layout(raw_dataframe: pd.DataFrame) -> bool:
    if len(raw_dataframe) < 3:
        return False

    first_row = {
        normalize_header_name(value)
        for value in raw_dataframe.iloc[0].tolist()
        if clean_text(value)
    }
    second_row = {
        normalize_header_name(value)
        for value in raw_dataframe.iloc[1].tolist()
        if clean_text(value)
    }

    return (
        "pin" in first_row
        and "pinrestinput" in first_row
        and "pintitle1stlinebold" in second_row
        and "pintitle2ndline" in second_row
    )


def parse_two_section_pin_layout(raw_dataframe: pd.DataFrame) -> pd.DataFrame:
    data = raw_dataframe.iloc[2:].copy().reset_index(drop=True)
    if data.empty:
        raise HTTPException(status_code=400, detail="No data rows found in PIN section.")

    def get_column(index: int) -> pd.Series:
        if index >= data.shape[1]:
            return pd.Series([""] * len(data))
        return data.iloc[:, index].fillna("").astype(str).map(clean_text)

    parsed = pd.DataFrame(
        {
            "PIN NAME": get_column(0),
            "Quote": get_column(0),
            "PINREST INPUT": get_column(2),
            "Meta Title": get_column(3),
            "Meta Description": get_column(4),
            "Hashtags": get_column(5),
            "TAG TOPIC": get_column(1),
            "CREATOR": "",
            "Timing Link": get_column(6),
        }
    )

    parsed = parsed[(parsed["PIN NAME"] != "") & (parsed["Quote"] != "")].reset_index(drop=True)
    if parsed.empty:
        raise HTTPException(status_code=400, detail="No valid rows with PIN NAME and Quote found in PIN section.")

    return parsed


def detect_header_row(raw_dataframe: pd.DataFrame) -> int | None:
    scan_limit = min(len(raw_dataframe), 12)
    header_vocabulary = set()
    for aliases in HEADER_ALIASES.values():
        header_vocabulary.update(aliases)

    best_row = None
    best_score = -1

    for row_index in range(scan_limit):
        row_values = {
            normalize_header_name(value)
            for value in raw_dataframe.iloc[row_index].tolist()
            if clean_text(value)
        }
        if not row_values:
            continue

        score = len(row_values.intersection(header_vocabulary))
        if "quote" in row_values:
            score += 6
        if "pinname" in row_values:
            score += 4
        has_primary_marker = "pinname" in row_values or "pin" in row_values

        if has_primary_marker and score > best_score:
            best_score = score
            best_row = row_index

    return best_row if best_score >= 3 else None


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
            if canonical in MANDATORY_COLUMNS:
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
        source_column = resolved_columns.get(canonical)
        if source_column:
            parsed[canonical] = dataframe[source_column].fillna("").astype(str).map(clean_text)
        else:
            parsed[canonical] = ""

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
    if parsed["Meta Title"].eq("").all():
        parsed["Meta Title"] = parsed["PINREST INPUT"].where(parsed["PINREST INPUT"] != "", parsed["PIN NAME"])

    if parsed["Meta Description"].eq("").all():
        parsed["Meta Description"] = parsed["Quote"]

    if not parsed.empty:
        first_row_text = " ".join(parsed.iloc[0].astype(str).tolist()).lower()
        if "pin title" in first_row_text and "pin description" in first_row_text:
            parsed = parsed.iloc[1:].reset_index(drop=True)

    parsed = parsed[parsed["PIN NAME"] != ""].reset_index(drop=True)

    if parsed.empty:
        raise HTTPException(status_code=400, detail="No valid rows found after parsing. Please verify your header row.")

    return parsed


def parse_file(file_name: str, file_bytes: bytes) -> pd.DataFrame:
    raw = read_tabular_file(file_name, file_bytes, header=None)
    if is_two_section_pin_layout(raw):
        return parse_two_section_pin_layout(raw)

    initial = read_tabular_file(file_name, file_bytes, header=0)
    has_unnamed = any(str(column).lower().startswith("unnamed") for column in initial.columns)

    if has_unnamed:
        header_row = detect_header_row(raw)
        if header_row is not None:
            header_values = [clean_text(value) for value in raw.iloc[header_row].tolist()]
            rebuilt = raw.iloc[header_row + 1 :].copy().reset_index(drop=True)
            rebuilt.columns = header_values
            return standardize_dataframe(rebuilt)

    try:
        return standardize_dataframe(initial)
    except HTTPException as first_error:
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


def get_quote_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    dancing_script = ROOT_DIR / "assets" / "fonts" / "DancingScript-Bold.ttf"
    candidates = [
        str(dancing_script),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    for candidate in candidates:
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

    selected_font = get_quote_font(72)
    lines = wrap_text(draw, quote_text, selected_font, quote_area_width)
    line_height = 80
    while (len(lines) > 6 or max(len(line) for line in lines) > 55) and line_height > 56:
        line_height -= 6
        selected_font = get_quote_font(line_height)
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
        draw.text((x + 2, y + 2), line, font=selected_font, fill=(0, 0, 0, 165))
        draw.text((x, y), line, font=selected_font, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(255, 255, 255, 255))

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
    background_image: Image.Image,
    generation_mode: str,
) -> Dict[str, Any]:
    pin_name = clean_text(row.get("PIN NAME"))
    quote = clean_text(row.get("Quote")) or pin_name
    prompt = build_ai_prompt(
        row.get("Meta Title", ""),
        row.get("Meta Description", ""),
        row.get("TAG TOPIC", ""),
    )

    rendered = render_pin(background_image, quote, text_position)
    base_slug = slugify_filename(pin_name or quote, index)
    file_name = make_unique_filename(session_dir, base_slug)
    file_path = session_dir / file_name
    rendered.save(file_path, format="PNG")

    return {
        "pin_id": str(uuid.uuid4()),
        "session_id": session_id,
        "pin_name": pin_name,
        "pic_no": normalize_pic_no(row.get("PIC NO.")),
        "quote": quote,
        "pinrest_input": clean_text(row.get("PINREST INPUT")),
        "meta_title": clean_text(row.get("Meta Title")),
        "meta_description": clean_text(row.get("Meta Description")),
        "hashtags": clean_text(row.get("Hashtags")),
        "tag_topic": clean_text(row.get("TAG TOPIC")),
        "creator": clean_text(row.get("CREATOR")),
        "timing_link": clean_text(row.get("Timing Link")),
        "ai_prompt": prompt,
        "mode": generation_mode,
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


@api_router.get("/health")
async def health():
    return {"status": "ok"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    if db is None:
        raise HTTPException(status_code=503, detail="Database is not configured (MONGO_URL unset).")
    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)

    # Convert to dict and serialize datetime to ISO string for MongoDB
    doc = status_obj.model_dump()
    doc["timestamp"] = doc["timestamp"].isoformat()

    _ = await db.status_checks.insert_one(doc)
    return status_obj


@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    if db is None:
        return []
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
    mode: str = Form("ai"),
    template_image: UploadFile | None = File(default=None),
    template_text_position: str = Form("center"),
    max_pins: int = Form(50),
    quotes_file: UploadFile | None = File(default=None),
    custom_images: List[UploadFile] = File(default=[]),
    custom_image_zip: UploadFile | None = File(default=None),
    image_links: str = Form(""),
    mapping_strategy: str = Form("pin_name_match_then_sequential"),
):
    mode = clean_text(mode).lower()
    if mode not in {"ai", "custom"}:
        raise HTTPException(status_code=400, detail="mode must be 'ai' or 'custom'")

    if max_pins < 1:
        raise HTTPException(status_code=400, detail="max_pins must be at least 1")

    if template_text_position not in {"top", "center", "bottom"}:
        raise HTTPException(status_code=400, detail="template_text_position must be top, center, or bottom")

    total_limit = min(max_pins, 100)
    data_file_name = clean_text(data_file.filename)
    if not data_file_name.lower().endswith((".xlsx", ".csv")):
        raise HTTPException(
            status_code=400,
            detail="Primary metadata file must be .xlsx or .csv. Use quotes_file for .docx uploads.",
        )

    file_bytes = await data_file.read()
    dataframe = parse_file(data_file_name, file_bytes)

    if dataframe.empty:
        raise HTTPException(status_code=400, detail="Uploaded file has no rows")

    dataframe = dataframe.head(total_limit)
    records = dataframe.to_dict(orient="records")
    total_rows_processed = len(records)

    if quotes_file is not None and quotes_file.filename:
        if not quotes_file.filename.lower().endswith(".docx"):
            raise HTTPException(status_code=400, detail="quotes_file must be a .docx Word document")
        quote_lines = parse_docx_quotes(await quotes_file.read())
        for index, row in enumerate(records):
            if index < len(quote_lines):
                row["Quote"] = quote_lines[index]

    valid_records: List[Dict[str, Any]] = []
    skipped_warnings: List[str] = []

    for row_index, row in enumerate(records, start=1):
        pin_name_value = clean_text(row.get("PIN NAME"))
        quote_value = clean_text(row.get("Quote"))
        pic_no_value = normalize_pic_no(row.get("PIC NO."))

        if mode == "custom" and not pic_no_value:
            skipped_warnings.append(f"Skipped row {row_index}: missing or invalid PIC NO.")
            continue

        if not pin_name_value or not quote_value:
            skipped_warnings.append(
                f"Skipped row {row_index}: missing {'PIN NAME' if not pin_name_value else ''}{' and ' if (not pin_name_value and not quote_value) else ''}{'Quote' if not quote_value else ''}."
            )
            continue

        row["PIN NAME"] = pin_name_value
        row["Quote"] = quote_value
        row["PIC NO."] = pic_no_value
        valid_records.append(row)

    if not valid_records:
        raise HTTPException(status_code=400, detail="No valid rows found with both PIN NAME and Quote.")

    records = valid_records

    session_id = str(uuid.uuid4())
    session_dir = GENERATED_PINS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    mode_used = mode
    auto_switched = False
    switch_message = ""

    template_obj = None
    if template_image is not None and template_image.filename:
        template_bytes = await template_image.read()
        try:
            template_obj = Image.open(io.BytesIO(template_bytes)).convert("RGB")
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid template image") from exc

    custom_file_entries: List[Dict[str, Any]] = []
    for uploaded_image in custom_images or []:
        if uploaded_image and uploaded_image.filename:
            custom_file_entries.append(
                {
                    "filename": uploaded_image.filename,
                    "content": await uploaded_image.read(),
                }
            )

    if custom_image_zip is not None and custom_image_zip.filename:
        if not custom_image_zip.filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="custom_image_zip must be a .zip file")
        zip_entries = extract_zip_image_entries(await custom_image_zip.read())
        custom_file_entries.extend(zip_entries)

    custom_assets: List[Dict[str, Any]] = []
    if custom_file_entries or clean_text(image_links):
        custom_assets = await asyncio.to_thread(load_custom_image_assets, custom_file_entries, image_links)

    if template_obj is not None:
        row_backgrounds = [template_obj.copy() for _ in records]
        images_matched = len(records)
        missing_images_count = 0
    else:
        if mode == "custom":
            custom_result = await asyncio.to_thread(
                build_custom_row_backgrounds,
                records,
                custom_assets,
                mapping_strategy,
            )
            records = custom_result["records"]
            row_backgrounds = custom_result["backgrounds"]
            images_matched = custom_result["matched_count"]
            missing_images_count = custom_result["missing_count"]
            skipped_warnings.extend(custom_result["warnings"])
        else:
            try:
                row_backgrounds = await build_ai_row_backgrounds(records, session_id)
                images_matched = len(records)
                missing_images_count = 0
            except AIQuotaExceededError:
                if custom_assets:
                    custom_result = await asyncio.to_thread(
                        build_custom_row_backgrounds,
                        records,
                        custom_assets,
                        mapping_strategy,
                    )
                    records = custom_result["records"]
                    row_backgrounds = custom_result["backgrounds"]
                    images_matched = custom_result["matched_count"]
                    missing_images_count = custom_result["missing_count"]
                    skipped_warnings.extend(custom_result["warnings"])
                    mode_used = "custom"
                    auto_switched = True
                    switch_message = (
                        "Gemini quota/rate limit hit. We automatically switched to Custom mode "
                        "using your uploaded custom images/links."
                    )
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Gemini quota/rate limit reached and no custom images/links were provided "
                            "for auto-switch. Upload custom assets and retry."
                        ),
                    )
            except AIImageGenerationError as exc:
                if custom_assets:
                    custom_result = await asyncio.to_thread(
                        build_custom_row_backgrounds,
                        records,
                        custom_assets,
                        mapping_strategy,
                    )
                    records = custom_result["records"]
                    row_backgrounds = custom_result["backgrounds"]
                    images_matched = custom_result["matched_count"]
                    missing_images_count = custom_result["missing_count"]
                    skipped_warnings.extend(custom_result["warnings"])
                    mode_used = "custom"
                    auto_switched = True
                    switch_message = (
                        "Gemini image generation is currently unavailable. "
                        "We automatically switched to Custom mode using your uploaded images/links."
                    )
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"AI image generation failed: {clean_text(exc)[:220]}",
                    ) from exc

    generation_progress = {
        "generated_count": 0,
        "total_count": len(records),
        "completed": False,
    }
    GENERATION_TRACKER[session_id] = generation_progress
    progress_lock = asyncio.Lock()

    semaphore = asyncio.Semaphore(10)

    async def process_row(index: int, row: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            pin = await asyncio.to_thread(
                create_pin_payload,
                row,
                index,
                session_id,
                session_dir,
                template_text_position,
                row_backgrounds[index].copy(),
                mode_used,
            )
            async with progress_lock:
                generation_progress["generated_count"] += 1
            return pin

    tasks = [process_row(index, row) for index, row in enumerate(records)]
    generated = await asyncio.gather(*tasks)
    generation_progress["completed"] = True

    pin_docs = [dict(pin) for pin in generated]
    if db is not None:
        await db.pin_records.insert_many(pin_docs)
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
    else:
        await _remember_ephemeral_session(session_id, pin_docs)

    GENERATION_TRACKER.pop(session_id, None)

    return {
        "session_id": session_id,
        "total_generated": len(generated),
        "skipped_rows": len(skipped_warnings),
        "warnings": skipped_warnings[:50],
        "mode_used": mode_used,
        "auto_switched": auto_switched,
        "switch_message": switch_message,
        "total_rows_processed": total_rows_processed,
        "images_matched": images_matched,
        "missing_images_count": missing_images_count,
        "pins": [to_public_pin(pin) for pin in generated],
    }


@api_router.get("/pins/{session_id}", response_model=List[PinRecord])
async def get_generated_pins(session_id: str):
    if db is not None:
        pins = await db.pin_records.find(
            {"session_id": session_id}, {"_id": 0, "file_path": 0}
        ).to_list(length=2000)
        return pins
    async with _EPHEMERAL_LOCK:
        raw = _EPHEMERAL_PINS_BY_SESSION.get(session_id, [])
    return [to_public_pin(p) for p in raw]


@api_router.get("/pins/progress/{session_id}", response_model=GenerationSummary)
async def get_generation_progress(session_id: str):
    live_progress = GENERATION_TRACKER.get(session_id)
    if live_progress:
        return {
            "generated_count": int(live_progress.get("generated_count", 0)),
            "completed": bool(live_progress.get("completed", False)),
        }

    if db is not None:
        session = await db.generation_sessions.find_one({"session_id": session_id}, {"_id": 0})
        if session:
            return {
                "generated_count": int(session.get("generated_count", 0)),
                "completed": bool(session.get("completed", False)),
            }
    async with _EPHEMERAL_LOCK:
        ephemeral = _EPHEMERAL_PINS_BY_SESSION.get(session_id)
    if ephemeral is not None:
        return {"generated_count": len(ephemeral), "completed": True}
    raise HTTPException(status_code=404, detail="Session not found")


@api_router.get("/pins/download/{pin_id}")
async def download_pin(pin_id: str):
    if db is not None:
        document = await db.pin_records.find_one({"pin_id": pin_id}, {"_id": 0})
    else:
        async with _EPHEMERAL_LOCK:
            document = _EPHEMERAL_PIN_BY_ID.get(pin_id)
    if not document:
        raise HTTPException(status_code=404, detail="Pin not found")

    file_path = Path(document["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Pin file missing")

    return FileResponse(path=file_path, media_type="image/png", filename=document["filename"])


@api_router.get("/pins/download-all/{session_id}")
async def download_all_pins(session_id: str):
    if db is not None:
        documents = await db.pin_records.find({"session_id": session_id}, {"_id": 0}).to_list(length=2000)
    else:
        async with _EPHEMERAL_LOCK:
            documents = list(_EPHEMERAL_PINS_BY_SESSION.get(session_id, []))
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
    if db is not None:
        documents = await db.pin_records.find(
            {"session_id": session_id},
            {"_id": 0, "file_path": 0},
        ).to_list(length=2000)
    else:
        async with _EPHEMERAL_LOCK:
            raw = _EPHEMERAL_PINS_BY_SESSION.get(session_id, [])
        documents = [to_public_pin(p) for p in raw]

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

_frontend_static = os.environ.get("FRONTEND_STATIC_DIR", "").strip()
if _frontend_static:
    _static_path = Path(_frontend_static).resolve()
    if _static_path.is_dir():
        app.mount("/", StaticFiles(directory=str(_static_path), html=True), name="frontend")

_cors_raw = os.environ.get("CORS_ORIGINS", "*").strip()
_cors_origins = (
    ["*"]
    if _cors_raw == "*"
    else [o.strip() for o in _cors_raw.split(",") if o.strip()]
)
# Browsers reject Allow-Credentials: true with Allow-Origin: * — use explicit origins for split deploy.
_cors_credentials = "*" not in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_credentials=_cors_credentials,
    allow_origins=_cors_origins,
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
    if client is not None:
        client.close()