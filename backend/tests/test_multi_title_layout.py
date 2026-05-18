import io
from pathlib import Path

import pytest
from PIL import Image

from server import clean_text, is_multi_title_pin_layout, parse_file, parse_multi_title_pin_layout, read_tabular_file


USER_EXCEL = Path(r"c:\Users\shubh\Downloads\SHUBHAM - PINTEREST.xlsx")


@pytest.mark.skipif(not USER_EXCEL.is_file(), reason="User sample Excel not available")
def test_multi_title_excel_layout_detected_and_parsed():
    raw = read_tabular_file(str(USER_EXCEL.name), USER_EXCEL.read_bytes(), header=None)
    assert is_multi_title_pin_layout(raw) is True
    parsed = parse_multi_title_pin_layout(raw)
    assert len(parsed) >= 1
    first = parsed.iloc[0]
    assert clean_text(first["PIN NAME"])
    assert (
        clean_text(first["PIN TITLE - TOP"])
        or clean_text(first["PIN TITLE - CENTER"])
        or clean_text(first["PIN TITLE - BOTTOM"])
    )
    assert clean_text(first["PIN TITLE 2ND LINE"])


def test_parse_file_multi_title_minimal_xlsx(api_client, base_url, tmp_path):
    import pandas as pd

    rows = [
        ["", "TOP", "CENTER", "BOTTOM"],
        ["PIC NO.", "PIN TITLE- TOP", "PIN TITLE- CENTER", "PIN TITLE- BOTTOM", "END LINE", "PIN NAME"],
        ["1", "Headline Top", "Headline Center", "Headline Bottom", "Tap to learn more", "sample-pin"],
    ]
    frame = pd.DataFrame(rows)
    xlsx_path = tmp_path / "multi-title.xlsx"
    frame.to_excel(xlsx_path, index=False, header=False)

    parsed = parse_file(xlsx_path.name, xlsx_path.read_bytes())
    assert parsed.iloc[0]["PIN TITLE - TOP"] == "Headline Top"
    assert parsed.iloc[0]["PIN TITLE 2ND LINE"] == "Tap to learn more"

    template = Image.new("RGB", (1000, 2100), (200, 200, 220))
    buf = io.BytesIO()
    template.save(buf, format="PNG")
    buf.seek(0)

    files = {"data_file": (xlsx_path.name, xlsx_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    data = {
        "pin_size": "long",
        "title_count": "3",
        "title_slots": "top,center,bottom",
        "max_pins": "1",
    }
    response = api_client.post(f"{base_url}/api/pins/generate", files=files, data=data, timeout=120)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_generated"] == 1
    pin = payload["pins"][0]
    assert pin["pin_title_2nd_line"] == "Tap to learn more"
    assert pin["pin_size"] == "long"
