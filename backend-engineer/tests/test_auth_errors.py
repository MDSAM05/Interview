import pytest
from httpx import AsyncClient
from user_service.main import app as user_app


@pytest.mark.asyncio
async def test_wrong_credentials_and_missing_fields():
    async with AsyncClient(app=user_app, base_url="http://test") as client:
        # Ensure user exists
        await client.post("/register", data={"username": "bad", "password": "good"})
        # Wrong password
        resp = await client.post("/token", data={"username": "bad", "password": "wrong"})
        assert resp.status_code == 400
        data = resp.json()
        assert data.get("type") == "http_error"

        # Missing username
        resp = await client.post("/token", data={"password": "good"})
        assert resp.status_code in (400, 422)

        # Missing password
        resp = await client.post("/token", data={"username": "bad"})
        assert resp.status_code in (400, 422)
