import pytest
from httpx import AsyncClient
from order_service.main import app as order_app


@pytest.mark.asyncio
async def test_order_api_edge_cases():
    async with AsyncClient(app=order_app, base_url="http://test") as client:
        # Create order without auth
        resp = await client.post("/orders", data={"productname": "Widget", "product_id": 1, "quantity": 1})
        assert resp.status_code == 401

        # List orders without auth
        resp = await client.get("/orders")
        assert resp.status_code == 401

        # Delete order without auth
        resp = await client.delete("/orders/1")
        assert resp.status_code == 401
