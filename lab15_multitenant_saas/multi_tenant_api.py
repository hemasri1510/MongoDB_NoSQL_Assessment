"""
Lab 15.1 & Activity 15.1: Multi-Tenant Task Management SaaS API (FastAPI + MongoDB)
Features:
1. Document-level multi-tenant schema with compound index on (tenant_id, created_at) and (tenant_id, owner_id).
2. JWT authentication dependency extracting user_id, tenant_id, and role ('admin' | 'member').
3. Atomic MongoDB-backed rate limiter using a TTL-indexed `rate_limits` collection and find_one_and_update.
4. Activity 15.1 RBAC:
   - 'admin' users can list, create, update, and delete ALL tasks within their tenant_id.
   - Regular ('member') users can only view, update, and delete THEIR OWN tasks (owner_id == user_id) within their tenant_id.

Run:
  uvicorn multi_tenant_api:app --reload --port 8000
"""

import datetime
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import List, Literal, Optional

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.environ.get("MONGO_DB", "cti_multitenant_saas")
JWT_SECRET = os.environ.get("JWT_SECRET", "super-secret-saas-jwt-key-2026")
JWT_ALGORITHM = "HS256"
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX", "30"))
RATE_LIMIT_WINDOW_SEC = int(os.environ.get("RATE_LIMIT_WINDOW_SEC", "60"))

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_col = db["users"]
tasks_col = db["tasks"]
rate_limits_col = db["rate_limits"]

security = HTTPBearer()


def ensure_indexes_and_seed():
    """Creates compound tenant indexes, TTL rate-limit index, and demo tenant users."""
    # Compound indexes enforcing efficient tenant-isolated queries
    tasks_col.create_index([("tenant_id", ASCENDING), ("created_at", DESCENDING)], name="idx_tenant_created")
    tasks_col.create_index([("tenant_id", ASCENDING), ("owner_id", ASCENDING)], name="idx_tenant_owner")
    tasks_col.create_index([("tenant_id", ASCENDING), ("task_id", ASCENDING)], unique=True, name="idx_tenant_task_unique")

    users_col.create_index([("tenant_id", ASCENDING), ("user_id", ASCENDING)], unique=True)

    # TTL index on rate_limits.window_expires_at (expireAfterSeconds=0 deletes docs at exact expiry timestamp)
    rate_limits_col.create_index([("window_expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_rate_limit_window")
    rate_limits_col.create_index([("tenant_id", ASCENDING), ("window_key", ASCENDING)], unique=True)

    # Seed sample users across two tenants (tenant_alpha & tenant_beta) for testing
    demo_users = [
        {"user_id": "u_alpha_admin", "tenant_id": "tenant_alpha", "email": "admin@alpha.io", "role": "admin"},
        {"user_id": "u_alpha_alice", "tenant_id": "tenant_alpha", "email": "alice@alpha.io", "role": "member"},
        {"user_id": "u_alpha_bob", "tenant_id": "tenant_alpha", "email": "bob@alpha.io", "role": "member"},
        {"user_id": "u_beta_admin", "tenant_id": "tenant_beta", "email": "admin@beta.io", "role": "admin"},
    ]
    for u in demo_users:
        users_col.update_one(
            {"tenant_id": u["tenant_id"], "user_id": u["user_id"]},
            {"$setOnInsert": u},
            upsert=True,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_indexes_and_seed()
    yield
    client.close()


app = FastAPI(
    title="Lab 15.1: Multi-Tenant Task Management SaaS API",
    version="1.0.0",
    lifespan=lifespan,
)


# -------------------- Pydantic Schemas --------------------
class TokenRequest(BaseModel):
    user_id: str
    tenant_id: str
    role: Literal["admin", "member"] = "member"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tenant_id: str
    user_id: str
    role: str


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = ""
    status: Literal["TODO", "IN_PROGRESS", "DONE"] = "TODO"
    priority: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    assigned_to: Optional[str] = None  # Admin can assign to another user in same tenant


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    status: Optional[Literal["TODO", "IN_PROGRESS", "DONE"]] = None
    priority: Optional[Literal["LOW", "MEDIUM", "HIGH"]] = None


class TaskOut(BaseModel):
    task_id: str
    tenant_id: str
    owner_id: str
    title: str
    description: str
    status: str
    priority: str
    created_at: datetime.datetime


class TenantUserContext(BaseModel):
    user_id: str
    tenant_id: str
    role: Literal["admin", "member"]


# -------------------- JWT Dependency & Atomic Rate Limiter --------------------
def get_current_tenant_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> TenantUserContext:
    """Extracts and validates tenant_id, user_id, and role from the Bearer JWT."""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        tenant_id = payload.get("tenant_id")
        user_id = payload.get("user_id")
        role = payload.get("role", "member")
        if not tenant_id or not user_id or role not in ("admin", "member"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
        return TenantUserContext(user_id=user_id, tenant_id=tenant_id, role=role)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"JWT validation failed: {exc}")


def enforce_tenant_rate_limit(ctx: TenantUserContext = Depends(get_current_tenant_user)) -> TenantUserContext:
    """
    Atomically increments per-tenant request counter in MongoDB (`rate_limits` with TTL index)
    using `find_one_and_update` (findAndModify) and rejects with HTTP 429 if limit exceeded.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    bucket_epoch = int(time.time() // RATE_LIMIT_WINDOW_SEC) * RATE_LIMIT_WINDOW_SEC
    window_key = f"{ctx.tenant_id}:{bucket_epoch}"
    expires_at = datetime.datetime.fromtimestamp(bucket_epoch + RATE_LIMIT_WINDOW_SEC + 5, tz=datetime.timezone.utc)

    record = rate_limits_col.find_one_and_update(
        {"tenant_id": ctx.tenant_id, "window_key": window_key},
        {
            "$inc": {"count": 1},
            "$setOnInsert": {
                "tenant_id": ctx.tenant_id,
                "window_key": window_key,
                "timestamp": now,
                "window_expires_at": expires_at,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    if record["count"] > RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for tenant '{ctx.tenant_id}' ({RATE_LIMIT_MAX_REQUESTS} req/{RATE_LIMIT_WINDOW_SEC}s).",
        )
    return ctx


# -------------------- Auth Helper Endpoint --------------------
@app.post("/auth/token", response_model=TokenResponse, tags=["Auth"])
def issue_jwt_token(req: TokenRequest):
    """Issues a signed JWT embedding tenant_id, user_id, and RBAC role."""
    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "sub": req.user_id,
        "user_id": req.user_id,
        "tenant_id": req.tenant_id,
        "role": req.role,
        "iat": now,
        "exp": now + datetime.timedelta(hours=8),
    }
    token = jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return TokenResponse(
        access_token=token,
        tenant_id=req.tenant_id,
        user_id=req.user_id,
        role=req.role,
    )


# -------------------- Tenant-Isolated CRUD Endpoints + RBAC --------------------
@app.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED, tags=["Tasks"])
def create_task(body: TaskCreate, ctx: TenantUserContext = Depends(enforce_tenant_rate_limit)):
    """Creates a task bound strictly to ctx.tenant_id. Only admins may assign tasks to other users."""
    owner_id = ctx.user_id
    if body.assigned_to and body.assigned_to != ctx.user_id:
        if ctx.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Regular users can only create tasks owned by themselves.",
            )
        owner_id = body.assigned_to

    doc = {
        "task_id": f"TSK-{uuid.uuid4().hex[:8].upper()}",
        "tenant_id": ctx.tenant_id,
        "owner_id": owner_id,
        "title": body.title,
        "description": body.description or "",
        "status": body.status,
        "priority": body.priority,
        "created_at": datetime.datetime.now(datetime.timezone.utc),
    }
    tasks_col.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/tasks", response_model=List[TaskOut], tags=["Tasks"])
def list_tasks(ctx: TenantUserContext = Depends(enforce_tenant_rate_limit)):
    """
    Lists tasks with mandatory tenant_id filter:
    - admin: sees all tasks in ctx.tenant_id
    - member: sees only tasks in ctx.tenant_id where owner_id == ctx.user_id
    """
    query = {"tenant_id": ctx.tenant_id}
    if ctx.role != "admin":
        query["owner_id"] = ctx.user_id

    cursor = tasks_col.find(query, {"_id": 0}).sort("created_at", DESCENDING)
    return list(cursor)


@app.get("/tasks/{task_id}", response_model=TaskOut, tags=["Tasks"])
def get_task(task_id: str, ctx: TenantUserContext = Depends(enforce_tenant_rate_limit)):
    query = {"tenant_id": ctx.tenant_id, "task_id": task_id}
    if ctx.role != "admin":
        query["owner_id"] = ctx.user_id

    doc = tasks_col.find_one(query, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found or access denied")
    return doc


@app.patch("/tasks/{task_id}", response_model=TaskOut, tags=["Tasks"])
def update_task(task_id: str, body: TaskUpdate, ctx: TenantUserContext = Depends(enforce_tenant_rate_limit)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields provided for update")

    query = {"tenant_id": ctx.tenant_id, "task_id": task_id}
    if ctx.role != "admin":
        query["owner_id"] = ctx.user_id

    updated = tasks_col.find_one_and_update(
        query,
        {"$set": updates},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found or insufficient RBAC permissions")
    return updated


@app.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Tasks"])
def delete_task(task_id: str, ctx: TenantUserContext = Depends(enforce_tenant_rate_limit)):
    query = {"tenant_id": ctx.tenant_id, "task_id": task_id}
    if ctx.role != "admin":
        query["owner_id"] = ctx.user_id

    res = tasks_col.delete_one(query)
    if res.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found or insufficient RBAC permissions")
    return None
