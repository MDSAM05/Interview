import os
import json
import logging
import threading
import datetime
from typing import Any, Dict, List

from fastapi import FastAPI, Depends, HTTPException, status, Request, Query, Body
from fastapi.security import OAuth2PasswordBearer
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse
from jose import JWTError, jwt
import asyncpg
import pika
import redis.asyncio as aioredis
from dotenv import load_dotenv
from pydantic import BaseModel

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL") or f"postgresql://{os.getenv('POSTGRES_USER', 'interview_user')}:{os.getenv('POSTGRES_PASSWORD', 'interview_password')}@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'interview_db')}"
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM")

REDIS_URL = os.getenv("REDIS_URL", "redis://:redis_password@localhost:6379/0")

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_DEFAULT_USER", "interview_user")
RABBITMQ_PASS = os.getenv("RABBITMQ_DEFAULT_PASS", "interview_password")
RABBITMQ_VHOST = os.getenv("RABBITMQ_DEFAULT_VHOST", "/")

# ──────────────────────────────────────────────
# App and Security Setup
# ──────────────────────────────────────────────

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="http://localhost:8001/token")

# ──────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────

class ProductCreate(BaseModel):
    name: str
    quantity: int

# ──────────────────────────────────────────────
# Exception Handlers
# ──────────────────────────────────────────────

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"type": "validation_error", "detail": exc.errors()})

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"type": "http_error", "detail": exc.detail})

# ──────────────────────────────────────────────
# Startup and Shutdown Events
# ──────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    app.state.db = await asyncpg.create_pool(DATABASE_URL)
    app.state.redis = await aioredis.from_url(REDIS_URL)
    start_rabbitmq_consumer()

@app.on_event("shutdown")
async def shutdown():
    await app.state.db.close()
    if hasattr(app.state, 'redis'):
        await app.state.redis.close()

# ──────────────────────────────────────────────
# Utility Functions
# ──────────────────────────────────────────────

def serialize(obj):
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    return username

def start_rabbitmq_consumer():
    def _consume_orders():
        try:
            creds = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            params = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT, virtual_host=RABBITMQ_VHOST, credentials=creds)
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.exchange_declare(exchange='orders', exchange_type='fanout', durable=True)
            channel.queue_declare(queue='product_orders', durable=True)
            channel.queue_bind(exchange='orders', queue='product_orders')

            def _on_msg(ch, method, properties, body):
                try:
                    evt = json.loads(body.decode('utf-8'))
                    logging.getLogger("product-service").info("orders_event", extra={"message": evt})
                except Exception:
                    pass
                ch.basic_ack(delivery_tag=method.delivery_tag)

            channel.basic_consume(queue='product_orders', on_message_callback=_on_msg, auto_ack=False)
            channel.start_consuming()
        except Exception as e:
            logging.warning(f"RabbitMQ consumer failed: {e}")

    threading.Thread(target=_consume_orders, name="rmq-product-consumer", daemon=True).start()

# ──────────────────────────────────────────────
# API Routes
# ──────────────────────────────────────────────
@app.post("/products")
async def add_product(product: ProductCreate, user=Depends(get_current_user)) -> Dict[str, Any]:
    async with app.state.db.acquire() as conn:
        result = await conn.fetchrow(
            """INSERT INTO product_service.products (name, quantity) 
               VALUES ($1, $2) 
               RETURNING id, name, quantity""",
            product.name, product.quantity
        )

    return {"msg": "Product added", "data": dict(result)}


@app.get("/products")
async def list_products(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100)) -> List[Dict[str, Any]]:
    offset = (page - 1) * page_size
    cache_key = f"products:{page}:{page_size}"
    cached = await app.state.redis.get(cache_key)
    if cached:
        return json.loads(cached)
    async with app.state.db.acquire() as conn:
        products = await conn.fetch("SELECT * FROM product_service.products ORDER BY id LIMIT $1 OFFSET $2", page_size, offset)
        data = [dict(p) for p in products]
        await app.state.redis.set(cache_key, json.dumps(data, default=serialize), ex=30)
        return data

@app.get("/products/{product_id}")
async def get_product(product_id: int) -> Dict[str, Any]:
    async with app.state.db.acquire() as conn:
        product = await conn.fetchrow("SELECT * FROM product_service.products WHERE id=$1", product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        return dict(product)

@app.post("/inventory/reserve")
async def reserve_inventory(product_id: int, quantity: int, user=Depends(get_current_user)) -> Dict[str, str]:
    async with app.state.db.acquire() as conn:
        product = await conn.fetchrow("SELECT id, quantity FROM product_service.products WHERE id=$1", product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        if product["quantity"] < quantity:
            raise HTTPException(status_code=409, detail="Insufficient stock")
        await conn.execute("UPDATE product_service.products SET quantity = quantity - $1 WHERE id=$2", quantity, product_id)

    try:
        creds = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        params = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT, virtual_host=RABBITMQ_VHOST, credentials=creds)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.exchange_declare(exchange='inventory', exchange_type='fanout', durable=True)
        event = {"type": "InventoryReserved", "product_id": product_id, "quantity": quantity}
        channel.basic_publish(exchange='inventory', routing_key='', body=json.dumps(event).encode('utf-8'))
        connection.close()
    except Exception as e:
        logging.warning(f"Could not publish inventory event: {e}")

    return {"status": "reserved"}

@app.delete("/products/{product_id}")
async def delete_product(product_id: int, user=Depends(get_current_user)) -> Dict[str, str]:
    async with app.state.db.acquire() as conn:
        result = await conn.execute("DELETE FROM product_service.products WHERE id=$1", product_id)
        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Product not found")
    return {"msg": f"Product {product_id} deleted"}
