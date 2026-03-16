import csv
import io
import json
import re
import zipfile

import pytest
import pandas as pd
from PIL import Image


# Pin generation and metadata lifecycle API coverage
REQUIRED_COLUMNS = [
    "Quote",
    "Meta Title",
    "Meta Description",
    "Hashtags",
    "TAG TOPIC",
    "CREATOR",
    "Timing Link",
]


def _build_csv_bytes(rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=REQUIRED_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def _build_template_image_bytes():
    image = Image.new("RGB", (1000, 1500), (240, 240, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.fixture(scope="module")
def sample_rows():
    return [
        {
            "Quote": "Stay consistent and trust your process",
            "Meta Title": "Consistency Mindset",
            "Meta Description": "Build tiny habits that compound over time",
            "Hashtags": "#mindset #habits",
            "TAG TOPIC": "self growth",
            "CREATOR": "TEST_creator",
            "Timing Link": "https://example.com/a",
        },
        {
            "Quote": "One small action today can change tomorrow",
            "Meta Title": "Daily Progress",
            "Meta Description": "Incremental effort creates long-term momentum",
            "Hashtags": "#progress #daily",
            "TAG TOPIC": "motivation",
            "CREATOR": "TEST_creator",
            "Timing Link": "https://example.com/b",
        },
    ]


@pytest.fixture(scope="module")
def generated_session(api_client, base_url, sample_rows):
    csv_payload = _build_csv_bytes(sample_rows)
    files = {
        "data_file": ("pins.csv", io.BytesIO(csv_payload), "text/csv"),
    }
    data = {
        "template_text_position": "center",
        "max_pins": "500",
    }
    response = api_client.post(f"{base_url}/api/pins/generate", files=files, data=data, timeout=180)
    assert response.status_code == 200

    payload = response.json()
    assert isinstance(payload.get("session_id"), str) and payload["session_id"]
    assert payload.get("total_generated") == len(sample_rows)
    assert isinstance(payload.get("pins"), list)
    assert len(payload["pins"]) == len(sample_rows)

    first = payload["pins"][0]
    assert first["quote"] == sample_rows[0]["Quote"]
    assert first["meta_title"] == sample_rows[0]["Meta Title"]
    assert first["meta_description"] == sample_rows[0]["Meta Description"]
    assert first["tag_topic"] == sample_rows[0]["TAG TOPIC"]
    assert "inspired by 'Consistency Mindset'" in first["ai_prompt"]
    assert first["image_url"].startswith("/api/static/pins/")
    assert "file_path" not in first

    expected_slug = "stay-consistent-and-trust-your-process"
    assert first["filename"].startswith(expected_slug)
    assert first["filename"].endswith(".png")
    assert re.match(r"^[a-z0-9-]+\.png$", first["filename"]) is not None

    return {
        "session_id": payload["session_id"],
        "pins": payload["pins"],
    }


def test_api_root_health(api_client, base_url):
    response = api_client.get(f"{base_url}/api/", timeout=30)
    assert response.status_code == 200
    assert response.json().get("message") == "Hello World"


def test_generate_rejects_missing_columns(api_client, base_url):
    invalid_csv = "Quote,Meta Title\nonly quote,only title\n".encode("utf-8")
    files = {
        "data_file": ("invalid.csv", io.BytesIO(invalid_csv), "text/csv"),
    }
    data = {
        "template_text_position": "center",
        "max_pins": "10",
    }
    response = api_client.post(f"{base_url}/api/pins/generate", files=files, data=data, timeout=30)
    assert response.status_code == 400
    assert "Missing columns" in response.json().get("detail", "")


def test_generate_supports_template_and_text_position(api_client, base_url, sample_rows):
    template_image = _build_template_image_bytes()
    csv_payload = _build_csv_bytes(sample_rows[:1])

    files = {
        "data_file": ("pins.csv", io.BytesIO(csv_payload), "text/csv"),
        "template_image": ("template.png", io.BytesIO(template_image), "image/png"),
    }
    data = {
        "template_text_position": "top",
        "max_pins": "500",
    }
    response = api_client.post(f"{base_url}/api/pins/generate", files=files, data=data, timeout=120)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_generated"] == 1
    assert payload["pins"][0]["quote"] == sample_rows[0]["Quote"]


def test_generate_accepts_xlsx_upload(api_client, base_url, sample_rows):
    dataframe = pd.DataFrame(sample_rows[:1])
    buffer = io.BytesIO()
    dataframe.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)

    files = {
        "data_file": ("pins.xlsx", buffer, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    }
    data = {
        "template_text_position": "bottom",
        "max_pins": "1",
    }
    response = api_client.post(f"{base_url}/api/pins/generate", files=files, data=data, timeout=120)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_generated"] == 1
    assert payload["pins"][0]["quote"] == sample_rows[0]["Quote"]


def test_get_pins_persistence(api_client, base_url, generated_session, sample_rows):
    session_id = generated_session["session_id"]
    response = api_client.get(f"{base_url}/api/pins/{session_id}", timeout=30)
    assert response.status_code == 200

    pins = response.json()
    assert len(pins) == len(sample_rows)
    assert pins[0]["session_id"] == session_id
    assert pins[0]["quote"] == sample_rows[0]["Quote"]
    assert "file_path" not in pins[0]


def test_get_progress(api_client, base_url, generated_session, sample_rows):
    session_id = generated_session["session_id"]
    response = api_client.get(f"{base_url}/api/pins/progress/{session_id}", timeout=30)
    assert response.status_code == 200
    payload = response.json()
    assert payload["completed"] is True
    assert payload["generated_count"] == len(sample_rows)


def test_download_individual_pin(api_client, base_url, generated_session):
    pin_id = generated_session["pins"][0]["pin_id"]
    response = api_client.get(f"{base_url}/api/pins/download/{pin_id}", timeout=30)
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("image/png")
    assert len(response.content) > 0


def test_download_zip_for_session(api_client, base_url, generated_session, sample_rows):
    session_id = generated_session["session_id"]
    response = api_client.get(f"{base_url}/api/pins/download-all/{session_id}", timeout=60)
    assert response.status_code == 200
    assert "application/zip" in response.headers.get("content-type", "")

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert len(names) == len(sample_rows)
    assert all(name.endswith(".png") for name in names)


def test_export_metadata_csv_and_json(api_client, base_url, generated_session):
    session_id = generated_session["session_id"]

    csv_response = api_client.get(
        f"{base_url}/api/pins/export/{session_id}?export_format=csv",
        timeout=30,
    )
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers.get("content-type", "")
    csv_text = csv_response.content.decode("utf-8")
    assert "ai_prompt" in csv_text
    assert "quote" in csv_text

    json_response = api_client.get(
        f"{base_url}/api/pins/export/{session_id}?export_format=json",
        timeout=30,
    )
    assert json_response.status_code == 200
    assert "application/json" in json_response.headers.get("content-type", "")
    data = json.loads(json_response.content.decode("utf-8"))
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["session_id"] == session_id
    assert "ai_prompt" in data[0]
