import os
import json
import logging
import asyncio
import threading

import pika
import httpx
import asyncpg
from jose import JWTError, jwt
from dotenv import load_dotenv
from typing import Any, Dict, List

from fastapi import FastAPI, Depends, HTTPException, status, Request, Body
from fastapi.security import OAuth2PasswordBearer
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

# ──────────────────────────────────────────────
# Configuration and Initialization
# ──────────────────────────────────────────────

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL") or f"postgresql://{os.getenv('POSTGRES_USER', 'interview_user')}:{os.getenv('POSTGRES_PASSWORD', 'interview_password')}@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'interview_db')}"

if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_DEFAULT_USER", "interview_user")
RABBITMQ_PASS = os.getenv("RABBITMQ_DEFAULT_PASS", "interview_password")
RABBITMQ_VHOST = os.getenv("RABBITMQ_DEFAULT_VHOST", "/")

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM")

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="http://localhost:8001/token")

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
# Database Setup
# ──────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    app.state.db = await asyncpg.create_pool(DATABASE_URL)
    await ensure_order_table()

@app.on_event("shutdown")
async def shutdown():
    await app.state.db.close()

async def ensure_order_table():
    async with app.state.db.acquire() as conn:
        try:
            await conn.execute("""
                ALTER TABLE order_service.orders 
                ADD COLUMN IF NOT EXISTS productname VARCHAR(255);
            """)
        except Exception as e:
            logging.warning(f"Could not ensure 'productname' column: {e}")

# ──────────────────────────────────────────────
# Authentication Dependency
# ──────────────────────────────────────────────

async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)) -> str:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    return username

# ──────────────────────────────────────────────
# Order Routes
# ──────────────────────────────────────────────

@app.post("/orders")
async def create_order(
    productname: str = Body(...),
    product_id: int = Body(...),
    quantity: int = Body(...),
    request: Request = None,
    user: str = Depends(get_current_user)
) -> Dict[str, Any]:

    await reserve_inventory(request, product_id, quantity)
    order = await insert_order(productname, product_id, quantity, user)
    publish_order_event(user, product_id, quantity)

    return {"msg": "Order placed", "data": order}


async def reserve_inventory(request: Request, product_id: int, quantity: int):
    reserve_url = "http://localhost:8001/inventory/reserve"
    timeout = httpx.Timeout(5.0, read=5.0)
    backoff = [0.2, 0.5, 1.0]
    last_exc = None

    for delay in backoff:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                headers = {}
                auth_header = request.headers.get("Authorization") if request else None
                if auth_header:
                    headers["Authorization"] = auth_header
                resp = await client.post(reserve_url, data={"product_id": product_id, "quantity": quantity}, headers=headers)
                if resp.status_code == 200:
                    return
                if resp.status_code in (404, 409):
                    raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail"))
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(delay)

    if last_exc:
        raise HTTPException(status_code=502, detail="Inventory service unavailable")

async def insert_order(productname: str, product_id: int, quantity: int, username: str) -> Dict[str, Any]:
    async with app.state.db.acquire() as conn:
        # Perform the insert and return the inserted row
        result = await conn.fetchrow("""
            INSERT INTO order_service.orders (productname, product_id, quantity, username, status)
            VALUES ($1, $2, $3, $4, 'CONFIRMED')
            RETURNING id, productname, product_id, quantity, username, status
        """, productname, product_id, quantity, username)
        return dict(result)

def publish_order_event(username: str, product_id: int, quantity: int):
    try:
        creds = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        params = pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            port=RABBITMQ_PORT,
            virtual_host=RABBITMQ_VHOST,
            credentials=creds
        )
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.exchange_declare(exchange='orders', exchange_type='fanout', durable=True)
        event = {
            "type": "OrderCreated",
            "username": username,
            "product_id": product_id,
            "quantity": quantity
        }
        channel.basic_publish(
            exchange='orders',
            routing_key='',
            body=json.dumps(event).encode('utf-8')
        )
        connection.close()
    except Exception as e:
        logging.warning(f"Could not publish order event: {e}")


@app.delete("/orders/{order_id}")
async def delete_order(order_id: int, user: str = Depends(get_current_user)):
    async with app.state.db.acquire() as conn:
        result = await conn.execute("DELETE FROM order_service.orders WHERE id=$1", order_id)
        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Order not found")
    return {"msg": f"Order {order_id} deleted"}

@app.get("/orders")
async def list_orders(user: str = Depends(get_current_user)) -> List[Dict[str, Any]]:
    async with app.state.db.acquire() as conn:
        orders = await conn.fetch("SELECT * FROM order_service.orders WHERE username=$1", user)
        return [dict(order) for order in orders]

# ──────────────────────────────────────────────
# RabbitMQ Consumer
# ──────────────────────────────────────────────

@app.on_event("startup")
async def start_consumer():
    threading.Thread(target=consume_inventory_events, name="rmq-order-consumer", daemon=True).start()

def consume_inventory_events():
    try:
        creds = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        params = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT, virtual_host=RABBITMQ_VHOST, credentials=creds)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.exchange_declare(exchange='inventory', exchange_type='fanout', durable=True)
        q = channel.queue_declare(queue='order_inventory', durable=True)
        channel.queue_bind(exchange='inventory', queue='order_inventory')

        def on_message(ch, method, properties, body):
            try:
                evt = json.loads(body.decode('utf-8'))
                logging.getLogger("order-service").info("inventory_event", extra={"message": evt})
            except Exception as e:
                logging.warning(f"Could not process inventory message: {e}")
            ch.basic_ack(delivery_tag=method.delivery_tag)

        channel.basic_consume(queue='order_inventory', on_message_callback=on_message, auto_ack=False)
        channel.start_consuming()
    except Exception as e:
        logging.warning(f"Inventory consumer failed: {e}")
