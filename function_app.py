import json
import logging
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

import azure.functions as func
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, func as sa_func, select
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ============================================================
# LOGGING
# ============================================================
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ============================================================
# AZURE FUNCTION APP
# ============================================================
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# ============================================================
# ENV / DATABASE CONFIG
# ============================================================
raw_database_url = os.getenv("DATABASE_URL")

if not raw_database_url:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Set it in local.settings.json for local, or in Azure Function App -> Configuration."
    )

if raw_database_url.startswith("postgresql://"):
    database_url = raw_database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif raw_database_url.startswith("postgres://"):
    database_url = raw_database_url.replace("postgres://", "postgresql+asyncpg://", 1)
else:
    database_url = raw_database_url

engine = create_async_engine(
    database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

_db_initialized = False

# ============================================================
# DATABASE MODELS
# ============================================================
class Base(DeclarativeBase):
    pass


class JobStatus(str, Enum):
    draft = "draft"
    active = "active"
    closed = "closed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String, nullable=False)
    required_skills: Mapped[List[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
    )
    experience_min: Mapped[int] = mapped_column(Integer, nullable=False)
    experience_max: Mapped[int] = mapped_column(Integer, nullable=False)
    max_candidates: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="jobstatus"),
        nullable=False,
        default=JobStatus.draft,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa_func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa_func.now(),
        nullable=False,
        onupdate=lambda: datetime.now(timezone.utc),
    )

# ============================================================
# PYDANTIC SCHEMAS
# ============================================================
class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    department: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)
    required_skills: List[str] = Field(default_factory=list)
    experience_min: int = Field(ge=0)
    experience_max: int = Field(ge=0)
    max_candidates: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_experience_range(self):
        if self.experience_max < self.experience_min:
            raise ValueError("experience_max must be >= experience_min")
        return self


class JobUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    department: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, min_length=1)
    required_skills: Optional[List[str]] = None
    experience_min: Optional[int] = Field(default=None, ge=0)
    experience_max: Optional[int] = Field(default=None, ge=0)
    max_candidates: Optional[int] = Field(default=None, gt=0)
    status: Optional[JobStatus] = None

    @model_validator(mode="after")
    def validate_experience_range(self):
        if self.experience_min is not None and self.experience_max is not None:
            if self.experience_max < self.experience_min:
                raise ValueError("experience_max must be >= experience_min")
        return self

# ============================================================
# HELPERS
# ============================================================
def job_to_dict(job: Job) -> dict:
    return {
        "id": str(job.id),
        "title": job.title,
        "department": job.department,
        "description": job.description,
        "required_skills": job.required_skills,
        "experience_min": job.experience_min,
        "experience_max": job.experience_max,
        "max_candidates": job.max_candidates,
        "status": job.status.value if isinstance(job.status, JobStatus) else str(job.status),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def json_response(
    data=None,
    status_code: int = 200,
    message: str = "OK",
    success: bool = True,
) -> func.HttpResponse:
    body = {
        "success": success,
        "message": message,
        "data": data,
    }
    return func.HttpResponse(
        body=json.dumps(body, default=str),
        status_code=status_code,
        mimetype="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        },
    )


def error_response(message: str, status_code: int, errors=None) -> func.HttpResponse:
    return json_response(
        data={"errors": errors} if errors else None,
        status_code=status_code,
        message=message,
        success=False,
    )


def parse_json_body(req: func.HttpRequest):
    try:
        body = req.get_json()
    except ValueError:
        return None, error_response("Invalid or missing JSON body.", 400)

    if not isinstance(body, dict):
        return None, error_response("Request body must be a JSON object.", 400)

    return body, None


def parse_uuid_or_error(value: str):
    try:
        return uuid.UUID(value), None
    except (ValueError, TypeError):
        return None, error_response(f"'{value}' is not a valid UUID.", 422)


def validation_error_response(exc: ValidationError) -> func.HttpResponse:
    return error_response("Validation failed", 422, errors=json.loads(exc.json()))


async def get_job_or_none(db: AsyncSession, job_id: uuid.UUID):
    result = await db.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one_or_none()


def validate_status_transition(current_status: JobStatus, new_status: JobStatus):
    allowed = {
        JobStatus.draft: {JobStatus.active},
        JobStatus.active: {JobStatus.closed},
        JobStatus.closed: set(),
    }
    if new_status != current_status and new_status not in allowed[current_status]:
        raise ValueError(f"Invalid transition: {current_status.value} -> {new_status.value}")


async def init_db():
    global _db_initialized
    if _db_initialized:
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    _db_initialized = True

# ============================================================
# OPENAPI
# ============================================================
OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Jobs API",
        "version": "1.0.0",
        "description": "Azure Functions Jobs API with JSON-only responses",
    },
    "servers": [
        {"url": "http://localhost:7071/api", "description": "Local"},
    ],
    "paths": {
        "/health": {
            "get": {
                "summary": "Health check",
                "operationId": "healthCheck",
                "responses": {
                    "200": {
                        "description": "Service is healthy",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/HealthResponse"}
                            }
                        },
                    }
                },
            }
        },
        "/jobs": {
            "post": {
                "summary": "Create a job",
                "operationId": "createJob",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/JobCreate"}
                        }
                    },
                },
                "responses": {
                    "201": {
                        "description": "Job created successfully",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/JobResponse"}
                            }
                        },
                    },
                    "400": {
                        "description": "Invalid request body",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        },
                    },
                    "422": {
                        "description": "Validation failed",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        },
                    },
                },
            },
            "get": {
                "summary": "List jobs",
                "operationId": "listJobs",
                "parameters": [
                    {
                        "name": "status",
                        "in": "query",
                        "required": False,
                        "schema": {
                            "type": "string",
                            "enum": ["draft", "active", "closed"],
                        },
                    },
                    {
                        "name": "page",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer", "default": 1, "minimum": 1},
                    },
                    {
                        "name": "page_size",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                    },
                ],
                "responses": {
                    "200": {
                        "description": "Jobs fetched successfully",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/JobListResponse"}
                            }
                        },
                    },
                    "422": {
                        "description": "Invalid query parameters",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        },
                    },
                },
            },
        },
        "/jobs/{job_id}": {
            "get": {
                "summary": "Get a job",
                "operationId": "getJob",
                "parameters": [
                    {
                        "name": "job_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Job fetched successfully",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/JobResponse"}
                            }
                        },
                    },
                    "404": {
                        "description": "Job not found",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        },
                    },
                    "422": {
                        "description": "Invalid UUID",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        },
                    },
                },
            },
            "patch": {
                "summary": "Update a job",
                "operationId": "updateJob",
                "parameters": [
                    {
                        "name": "job_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/JobUpdate"}
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Job updated successfully",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/JobResponse"}
                            }
                        },
                    },
                    "404": {
                        "description": "Job not found",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        },
                    },
                    "422": {
                        "description": "Validation failed",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        },
                    },
                },
            },
            "delete": {
                "summary": "Delete a job",
                "operationId": "deleteJob",
                "parameters": [
                    {
                        "name": "job_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Job deleted successfully",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/DeleteResponse"}
                            }
                        },
                    },
                    "404": {
                        "description": "Job not found",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        },
                    },
                    "422": {
                        "description": "Invalid UUID",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        },
                    },
                },
            },
        },
    },
    "components": {
        "schemas": {
            "Job": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "title": {"type": "string"},
                    "department": {"type": "string"},
                    "description": {"type": "string"},
                    "required_skills": {"type": "array", "items": {"type": "string"}},
                    "experience_min": {"type": "integer"},
                    "experience_max": {"type": "integer"},
                    "max_candidates": {"type": "integer"},
                    "status": {"type": "string", "enum": ["draft", "active", "closed"]},
                    "created_at": {"type": "string", "format": "date-time"},
                    "updated_at": {"type": "string", "format": "date-time"},
                },
            },
            "JobCreate": {
                "type": "object",
                "required": [
                    "title",
                    "department",
                    "description",
                    "required_skills",
                    "experience_min",
                    "experience_max",
                    "max_candidates",
                ],
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 255},
                    "department": {"type": "string", "minLength": 1, "maxLength": 255},
                    "description": {"type": "string", "minLength": 1},
                    "required_skills": {"type": "array", "items": {"type": "string"}},
                    "experience_min": {"type": "integer", "minimum": 0},
                    "experience_max": {"type": "integer", "minimum": 0},
                    "max_candidates": {"type": "integer", "minimum": 1},
                },
            },
            "JobUpdate": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 255},
                    "department": {"type": "string", "minLength": 1, "maxLength": 255},
                    "description": {"type": "string", "minLength": 1},
                    "required_skills": {"type": "array", "items": {"type": "string"}},
                    "experience_min": {"type": "integer", "minimum": 0},
                    "experience_max": {"type": "integer", "minimum": 0},
                    "max_candidates": {"type": "integer", "minimum": 1},
                    "status": {"type": "string", "enum": ["draft", "active", "closed"]},
                },
            },
            "BaseResponse": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string"},
                },
            },
            "JobResponse": {
                "allOf": [
                    {"$ref": "#/components/schemas/BaseResponse"},
                    {
                        "type": "object",
                        "properties": {
                            "data": {"$ref": "#/components/schemas/Job"}
                        },
                    },
                ]
            },
            "JobListData": {
                "type": "object",
                "properties": {
                    "total": {"type": "integer"},
                    "page": {"type": "integer"},
                    "page_size": {"type": "integer"},
                    "total_pages": {"type": "integer"},
                    "items": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Job"},
                    },
                },
            },
            "JobListResponse": {
                "allOf": [
                    {"$ref": "#/components/schemas/BaseResponse"},
                    {
                        "type": "object",
                        "properties": {
                            "data": {"$ref": "#/components/schemas/JobListData"}
                        },
                    },
                ]
            },
            "HealthResponse": {
                "allOf": [
                    {"$ref": "#/components/schemas/BaseResponse"},
                    {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "object",
                                "properties": {
                                    "status": {"type": "string"},
                                    "database": {"type": "string"},
                                    "environment": {"type": "string"},
                                },
                            }
                        },
                    },
                ]
            },
            "DeleteResponse": {
                "allOf": [
                    {"$ref": "#/components/schemas/BaseResponse"},
                    {
                        "type": "object",
                        "properties": {
                            "data": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"}
                                },
                            }
                        },
                    },
                ]
            },
            "ErrorResponse": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "example": False},
                    "message": {"type": "string"},
                    "data": {
                        "nullable": True,
                        "oneOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "properties": {
                                    "errors": {
                                        "type": "array",
                                        "items": {"type": "object"},
                                    }
                                },
                            },
                        ],
                    },
                },
            },
        }
    },
}

# ============================================================
# ROUTES
# ============================================================
@app.route(route="openapi.json", methods=["GET"])
async def openapi_spec(req: func.HttpRequest) -> func.HttpResponse:
    return json_response(OPENAPI_SPEC, 200, "OpenAPI fetched successfully")


@app.route(route="health", methods=["GET"])
async def health(req: func.HttpRequest) -> func.HttpResponse:
    try:
        await init_db()
        async with SessionLocal() as db:
            await db.execute(select(sa_func.count()).select_from(Job))

        return json_response(
            {
                "status": "healthy",
                "database": "connected",
                "environment": os.getenv("AZURE_FUNCTIONS_ENVIRONMENT", "unknown"),
            },
            200,
            "Health check successful",
        )
    except Exception as exc:
        logger.exception("Health check failed")
        return error_response(f"Database connection failed: {str(exc)}", 500)


@app.route(route="jobs", methods=["POST"])
async def create_job(req: func.HttpRequest) -> func.HttpResponse:
    body, err = parse_json_body(req)
    if err:
        return err

    try:
        payload = JobCreate(**body)
    except ValidationError as exc:
        return validation_error_response(exc)

    try:
        await init_db()
        async with SessionLocal() as db:
            job = Job(
                title=payload.title,
                department=payload.department,
                description=payload.description,
                required_skills=payload.required_skills,
                experience_min=payload.experience_min,
                experience_max=payload.experience_max,
                max_candidates=payload.max_candidates,
                status=JobStatus.draft,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)

        return json_response(job_to_dict(job), 201, "Job created successfully")
    except Exception as exc:
        logger.exception("create_job failed")
        return error_response(str(exc), 500)


@app.route(route="jobs", methods=["GET"])
async def list_jobs(req: func.HttpRequest) -> func.HttpResponse:
    status_filter = req.params.get("status")
    page_raw = req.params.get("page", "1")
    page_size_raw = req.params.get("page_size", "20")

    try:
        page = max(1, int(page_raw))
        page_size = max(1, min(100, int(page_size_raw)))
    except ValueError:
        return error_response("'page' and 'page_size' must be integers.", 422)

    try:
        async with SessionLocal() as db:
            query = select(Job).order_by(Job.created_at.desc())
            count_query = select(sa_func.count()).select_from(Job)

            if status_filter:
                try:
                    status_enum = JobStatus(status_filter)
                except ValueError:
                    return error_response("Invalid status. Use: draft, active, closed", 422)

                query = query.where(Job.status == status_enum)
                count_query = count_query.where(Job.status == status_enum)

            total = (await db.execute(count_query)).scalar_one()
            items = (
                await db.execute(
                    query.offset((page - 1) * page_size).limit(page_size)
                )
            ).scalars().all()

        return json_response(
            {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total else 0,
                "items": [job_to_dict(item) for item in items],
            },
            200,
            "Jobs fetched successfully",
        )
    except Exception as exc:
        logger.exception("list_jobs failed")
        return error_response(str(exc), 500)


@app.route(route="jobs/{job_id}", methods=["GET"])
async def get_job(req: func.HttpRequest) -> func.HttpResponse:
    job_id, err = parse_uuid_or_error(req.route_params.get("job_id"))
    if err:
        return err

    try:
        async with SessionLocal() as db:
            job = await get_job_or_none(db, job_id)
            if not job:
                return error_response("Job not found.", 404)

        return json_response(job_to_dict(job), 200, "Job fetched successfully")
    except Exception as exc:
        logger.exception("get_job failed")
        return error_response(str(exc), 500)


@app.route(route="jobs/{job_id}", methods=["PATCH"])
async def update_job(req: func.HttpRequest) -> func.HttpResponse:
    job_id, err = parse_uuid_or_error(req.route_params.get("job_id"))
    if err:
        return err

    body, err = parse_json_body(req)
    if err:
        return err

    try:
        payload = JobUpdate(**body)
    except ValidationError as exc:
        return validation_error_response(exc)

    try:
        async with SessionLocal() as db:
            job = await get_job_or_none(db, job_id)
            if not job:
                return error_response("Job not found.", 404)

            updates = payload.model_dump(exclude_unset=True)

            if "status" in updates and updates["status"] is not None:
                validate_status_transition(job.status, updates["status"])

            for field, value in updates.items():
                setattr(job, field, value)

            job.updated_at = datetime.now(timezone.utc)

            await db.commit()
            await db.refresh(job)

        return json_response(job_to_dict(job), 200, "Job updated successfully")
    except ValueError as exc:
        return error_response(str(exc), 422)
    except Exception as exc:
        logger.exception("update_job failed")
        return error_response(str(exc), 500)


@app.route(route="jobs/{job_id}", methods=["DELETE"])
async def delete_job(req: func.HttpRequest) -> func.HttpResponse:
    job_id, err = parse_uuid_or_error(req.route_params.get("job_id"))
    if err:
        return err

    try:
        async with SessionLocal() as db:
            job = await get_job_or_none(db, job_id)
            if not job:
                return error_response("Job not found.", 404)

            await db.delete(job)
            await db.commit()

        return json_response(
            {"message": "Job deleted successfully"},
            200,
            "Job deleted successfully",
        )
    except Exception as exc:
        logger.exception("delete_job failed")
        return error_response(str(exc), 500)


@app.route(route="{*route}", methods=["OPTIONS"])
async def options_handler(req: func.HttpRequest) -> func.HttpResponse:
    return json_response(None, 200, "CORS preflight OK")