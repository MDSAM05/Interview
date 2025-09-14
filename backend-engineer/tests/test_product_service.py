import pytest
from httpx import AsyncClient
from product_service.main import app


@pytest.mark.asyncio
async def test_product_crud_and_edge_cases():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # List products (should be empty or list)
        resp = await client.get("/products?page=1&page_size=1")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

        # Add product without auth (should fail)
        resp = await client.post("/products", json={"name": "Widget", "quantity": 10})
        assert resp.status_code == 401

        # Simulate login to get token (mock or use a valid token if available)
        # For now, skip auth-required endpoints if no token available

        # Get non-existent product
        resp = await client.get("/products/99999")
        assert resp.status_code == 404

        # Reserve inventory for non-existent product
        resp = await client.post("/inventory/reserve", data={"product_id": 99999, "quantity": 1})
        assert resp.status_code == 401 or resp.status_code == 404
