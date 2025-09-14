## API Gateway (Traefik) example (not added to compose)
## Testing

Install dev deps and run tests with coverage:

```
pip install -r requirements.txt
pytest --cov=.
```

## Performance notes

- Async stack throughout (FastAPI, asyncpg/httpx).  
- Redis caching for product list (short TTL).  
- DB indexes on usernames, product names, and order usernames.  
- Use multiple uvicorn workers for CPU-bound routes; scale horizontally behind a gateway.

## Deployment and configuration

- Configure `DATABASE_URL`, `JWT_*`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `REDIS_URL`, and RabbitMQ envs.
- Gateway: see Traefik example above. For Kubernetes, use Ingress and per-service Deployments.

Create `gateway/traefik.yml`:

```
entryPoints:
  web:
    address: ":8080"
providers:
  file:
    filename: "/etc/traefik/dynamic.yml"
```

Create `gateway/dynamic.yml`:

```
http:
  routers:
    user:
      rule: "PathPrefix(`/users`)"
      service: user
    product:
      rule: "PathPrefix(`/products`) || PathPrefix(`/inventory`)"
      service: product
    order:
      rule: "PathPrefix(`/orders`)"
      service: order
  services:
    user:
      loadBalancer:
        servers:
          - url: "http://host.docker.internal:8001"
    product:
      loadBalancer:
        servers:
          - url: "http://host.docker.internal:8002"
    order:
      loadBalancer:
        servers:
          - url: "http://host.docker.internal:8003"
```

Run Traefik separately if desired:

```
docker run -p 8080:8080 -v $PWD/gateway/traefik.yml:/etc/traefik/traefik.yml -v $PWD/gateway/dynamic.yml:/etc/traefik/dynamic.yml traefik:v3.0
```
# Interview Microservices Architecture

A comprehensive microservices-based e-commerce system built with FastAPI, Redis, RabbitMQ, and Docker Compose for interview demonstration.

## Architecture Overview

This project implements a microservices architecture with the following components:

### Core Services
Note: For local development in this setup, only infrastructure is containerized. You will run the FastAPI app locally on your host.

### Infrastructure Services
- **PostgreSQL**: Primary database (interview_db)
- **Redis**: Caching and session management
- **RabbitMQ**: Message queue with management UI
- **Postman/Newman**: Optional CLI for API collections

## Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Cache**: Redis
- **Message Queue**: RabbitMQ
- **Containerization**: Docker & Docker Compose
- **Containerization**: Docker & Docker Compose (infra only)
- **Authentication**: JWT tokens with bcrypt password hashing

## Features

### User Service
- User registration and authentication
- JWT-based authentication
- User profile management
- Password hashing with bcrypt
- Event-driven architecture for user events

### Product Service
- Product catalog management
- Inventory tracking and management
- Stock availability checking
- Product search and filtering
- Inventory transaction history
- Event-driven inventory updates

### Order Service
- Order creation and management
- Order status tracking
- Payment processing (simulated)
- Order cancellation
- Payment history
- Event-driven order processing

### API Gateway
- Centralized API entry point
- Request routing to appropriate services
- Load balancing
- CORS handling
- Health monitoring
- Error handling and logging

## Getting Started

### Prerequisites
- Docker and Docker Compose
- Git

### Quickstart (Infra only + Local FastAPI)

1. Clone the repository:
```bash
git clone <repository-url>
cd backend-engineer
```

2. Start infrastructure (Postgres, Redis, RabbitMQ, Newman/Postman):
```bash
docker-compose up -d
```

3. Create a `.env` file for the local FastAPI app in `backend-engineer/` with these values (host networking):
```env
DATABASE_URL=postgresql+asyncpg://interview_user:interview_password@localhost:5432/interview_db
REDIS_URL=redis://:redis_password@localhost:6379/0
RABBITMQ_URL=amqp://interview_user:interview_password@localhost:5672/interview_vhost
```

4. Run the example FastAPI app locally (items CRUD):
```bash
cd backend-engineer
uvicorn src.main:app --reload --port 9000
```

### Local URLs

- **FastAPI app (local)**: http://localhost:9000/docs
- **RabbitMQ Management**: http://localhost:15672 (interview_user/interview_password)
- **PostgreSQL**: localhost:5432 (DB: interview_db, user: interview_user, pass: interview_password)
- **Redis**: localhost:6379 (password: redis_password)

## API Documentation

### Authentication Endpoints

#### Register User
```bash
POST /auth/register
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "password123",
  "first_name": "John",
  "last_name": "Doe",
  "phone": "+1234567890"
}
```

#### Login User
```bash
POST /auth/login
Content-Type: application/x-www-form-urlencoded

email=user@example.com&password=password123
```

### Product Endpoints

#### Get Products
```bash
GET /products?category=electronics&search=laptop&skip=0&limit=10
```

#### Create Product
```bash
POST /products
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Laptop",
  "description": "High-performance laptop",
  "price": 999.99,
  "category": "electronics",
  "sku": "LAPTOP001",
  "stock_quantity": 50
}
```

### Order Endpoints

#### Create Order
```bash
POST /orders
Authorization: Bearer <token>
Content-Type: application/json

{
  "user_id": "user-uuid",
  "items": [
    {
      "product_id": "product-uuid",
      "quantity": 2,
      "price": 999.99
    }
  ],
  "shipping_address": {
    "street": "123 Main St",
    "city": "New York",
    "state": "NY",
    "zip": "10001",
    "country": "US"
  },
  "billing_address": {
    "street": "123 Main St",
    "city": "New York",
    "state": "NY",
    "zip": "10001",
    "country": "US"
  }
}
```

## Service Communication

The microservices communicate through:

1. **Synchronous Communication**: HTTP/REST API calls through the API Gateway
2. **Asynchronous Communication**: RabbitMQ message queues for events
3. **Data Sharing**: Shared PostgreSQL database with service-specific schemas

### Event Types

- `user.created`, `user.updated`, `user.deleted`
- `product.created`, `product.updated`, `product.deleted`, `inventory.updated`
- `order.created`, `order.updated`, `order.cancelled`, `payment.processed`

## Data Consistency

The system implements eventual consistency through:

- **Event Sourcing**: Services publish events for state changes
- **Saga Pattern**: Distributed transactions across services
- **Compensating Actions**: Rollback mechanisms for failed operations
- **Idempotency**: Operations can be safely retried

## Monitoring and Health Checks

Each service provides health check endpoints:
- `/health` - Service health status
- API Gateway aggregates health status from all services

## Development

### Running the sample FastAPI app

The sample app lives in `src/` and exposes:

- `POST /items` to add an item
- `GET /items` to list items

Run it with:
```bash
cd backend-engineer
uvicorn src.main:app --reload --port 9000
```

### Database Migrations

```bash
# Generate migration
alembic revision --autogenerate -m "Description"

# Apply migrations
alembic upgrade head
```

## Testing

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=.
```

## Production Considerations

1. **Security**:
   - Change default passwords and secrets
   - Implement proper CORS policies
   - Use HTTPS in production
   - Implement rate limiting

2. **Scalability**:
   - Use container orchestration (Kubernetes)
   - Implement horizontal scaling
   - Use database connection pooling
   - Implement caching strategies

3. **Monitoring**:
   - Add logging and monitoring
   - Implement distributed tracing
   - Set up alerting
   - Monitor performance metrics

4. **Data Management**:
   - Implement database backups
   - Use database replication
   - Implement data archiving
   - Monitor database performance

## License

This project is licensed under the MIT License.