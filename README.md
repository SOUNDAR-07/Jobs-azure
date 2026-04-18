# Jobs-azure

| Layer    | Responsibility |
| -------- | -------------- |
| ENV      | Config         |
| Database | Connection     |
| Model    | Table          |
| Schema   | Validation     |
| Route    | API            |
| Helper   | Reusable logic |

# Jobs API (Azure Functions + PostgreSQL)

An API built using Azure Functions, SQLAlchemy (async), and PostgreSQL.

## 🚀 Features

* Create, update, delete, and list jobs
* JSON-only responses
* OpenAPI support (`/api/openapi.json`)
* Async DB operations
* Pagination & filtering

## 📌 Endpoints

| Method | Endpoint          | Description  |
| ------ | ----------------- | ------------ |
| GET    | /api/health       | Health check |
| POST   | /api/jobs         | Create job   |
| GET    | /api/jobs         | List jobs    |
| GET    | /api/jobs/{id}    | Get job      |
| PATCH  | /api/jobs/{id}    | Update job   |
| DELETE | /api/jobs/{id}    | Delete job   |
| GET    | /api/openapi.json | OpenAPI spec |

## ⚙️ Setup

```bash
pip install -r requirements.txt
```

Set environment variable:

```bash
Example (Local PostgreSQL)
DATABASE_URL=postgresql+asyncpg://postgres:mysecretpassword@localhost:5432/jobs_db
Example (Cloud Database)
DATABASE_URL=postgresql+asyncpg://user:password@your-db-host.azure.com:5432/jobs_db

```

Run locally:

```bash
func start
```

## 🧪 Test

```bash
curl http://localhost:7071/api/health
```

## 📄 OpenAPI

```bash
http://localhost:7071/api/openapi.json
```

## 🛠 Tech Stack

* Azure Functions
* Python (Async)
* SQLAlchemy
* PostgreSQL
