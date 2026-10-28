from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idp.main import create_app
from idp.settings import Settings

USER1_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        key_path=str(tmp_path / "key.pem"),
        user1_username="alice",
        user1_password="alice-pw",
        user1_id=USER1_ID,
        user2_username="bob",
        user2_password="bob-pw",
        user2_id="22222222-2222-2222-2222-222222222222",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c
