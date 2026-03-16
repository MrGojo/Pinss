import os
import pytest
import requests


@pytest.fixture(scope="session")
def base_url():
    # Backend URL for all public endpoint testing
    url = os.environ.get("REACT_APP_BACKEND_URL")
    if not url:
        pytest.skip("REACT_APP_BACKEND_URL is not set")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def api_client():
    # Shared HTTP session for API requests
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    return session
