import io

import pytest
import requests


# Regression coverage for user-provided Excel header alias parsing + 50/100 batch limits
USER_FILE_URL = "https://customer-assets.emergentagent.com/job_excel-pin-factory/artifacts/flsa14de_test%20excel%20fior%20pinterest%20%281%29.xlsx"


@pytest.fixture(scope="module")
def user_excel_bytes():
    response = requests.get(USER_FILE_URL, timeout=60)
    response.raise_for_status()
    return response.content


def _generate_from_user_file(api_client, base_url, file_bytes, max_pins):
    files = {
        "data_file": (
            "user_input.xlsx",
            io.BytesIO(file_bytes),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    data = {
        "template_text_position": "center",
        "max_pins": str(max_pins),
    }
    return api_client.post(
        f"{base_url}/api/pins/generate",
        files=files,
        data=data,
        timeout=300,
    )


@pytest.mark.parametrize("max_pins", [50, 100])
def test_user_excel_alias_headers_generate_success(api_client, base_url, user_excel_bytes, max_pins):
    response = _generate_from_user_file(api_client, base_url, user_excel_bytes, max_pins)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert isinstance(payload.get("session_id"), str) and payload["session_id"]
    assert isinstance(payload.get("total_generated"), int)
    assert payload["total_generated"] > 0
    assert payload["total_generated"] <= max_pins

    pins = payload.get("pins")
    assert isinstance(pins, list)
    assert len(pins) == payload["total_generated"]

    first_pin = pins[0]
    assert isinstance(first_pin.get("quote"), str)
    assert isinstance(first_pin.get("meta_title"), str)
    assert "tag_topic" in first_pin
    assert "timing_link" in first_pin
    assert first_pin.get("image_url", "").startswith("/api/static/pins/")