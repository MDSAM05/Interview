import asyncio
import pytest
from httpx import AsyncClient
from fastapi import status
from user_service.main import app


@pytest.mark.asyncio
async def test_user_register_and_token():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Register new user
        resp = await client.post("/register", data={"username": "qauser", "password": "secret"})
        assert resp.status_code in (status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST)
        # Register same user again (should fail)
        resp2 = await client.post("/register", data={"username": "qauser", "password": "secret"})
        assert resp2.status_code == status.HTTP_400_BAD_REQUEST
        # Login
        resp = await client.post("/token", data={"username": "qauser", "password": "secret"})
        assert resp.status_code == status.HTTP_200_OK
        token = resp.json().get("access_token")
        assert token
        # Profile with token
        resp = await client.get("/profile", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == status.HTTP_200_OK
        # Profile with invalid token
        resp = await client.get("/profile", headers={"Authorization": "Bearer badtoken"})
        assert resp.status_code == 401
        # Debug token endpoint
        resp = await client.get("/debug/token", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_received"] is True
        # Delete user
        resp = await client.delete(f"/users/qauser", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        # Delete again (should 404)
        resp = await client.delete(f"/users/qauser", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404
        # Login after delete (should fail)
        resp = await client.post("/token", data={"username": "qauser", "password": "secret"})
        assert resp.status_code == 400
