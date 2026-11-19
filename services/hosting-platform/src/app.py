"""Hosting Platform API: user registration and website requests, stored in the platform database."""

import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from src.security import hash_password
from src.store import Conflict, NotFound

logger = logging.getLogger("hosting-platform")
TENANT_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,99}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _tenant(value):
    if not TENANT_RE.fullmatch(value):
        raise ValueError("Use 3–32 lowercase letters, numbers, and hyphens; start with a letter.")
    return value


def _email(value):
    value = value.strip().lower()
    if len(value) > 255 or not EMAIL_RE.fullmatch(value):
        raise ValueError("Enter a valid e-mail address.")
    return value


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def username_is_valid(cls, value):
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("Use 3–100 letters, numbers, '.', '_' or '-'; start with a letter or number.")
        return value

    @field_validator("email")
    @classmethod
    def email_is_valid(cls, value):
        return _email(value)


class WebsiteRequest(BaseModel):
    tenant: str
    domain: str
    email: str = Field(description="E-mail of the registered user who owns the website")
    name: str | None = Field(default=None, max_length=150)

    @field_validator("tenant")
    @classmethod
    def tenant_must_be_dns_safe(cls, value):
        return _tenant(value)

    @field_validator("domain")
    @classmethod
    def domain_must_be_a_hostname(cls, value):
        value = value.lower()
        if not DOMAIN_RE.fullmatch(value):
            raise ValueError("Enter a valid domain name, such as shop.example.com.")
        return value

    @field_validator("email")
    @classmethod
    def email_is_valid(cls, value):
        return _email(value)


def create_app(store=None, publish=None, kafka_ready=None, lifespan=None):
    """store: PlatformStore; publish(event): send to Kafka (raises on failure); kafka_ready(): bool."""

    @asynccontextmanager
    async def default_lifespan(_app):
        yield

    app = FastAPI(title="Hosting Platform API", version="1.0", lifespan=lifespan or default_lifespan)
    app.state.store = store
    app.state.publish = publish
    app.state.kafka_ready = kafka_ready or (lambda: True)

    def ready_store(request: Request):
        if request.app.state.store is None:
            raise HTTPException(status_code=503, detail="database_unavailable")
        return request.app.state.store

    Store = Annotated[object, Depends(ready_store)]

    def publish_event(request, store, event):
        """Send the event to Kafka and record the outcome; the request can simply be repeated if Kafka fails."""
        try:
            request.app.state.publish(event)
        except Exception as error:
            logger.exception("Could not publish %s event_id=%s", event["event_type"], event["event_id"])
            store.mark_published(event["event_id"], published=False)
            raise HTTPException(status_code=503, detail="event_not_published_retry_the_request") from error
        store.mark_published(event["event_id"], published=True)
        logger.info("published %s event_id=%s tenant=%s", event["event_type"], event["event_id"], event["tenant"])

    # ---- health
    @app.get("/health/live")
    def liveness():
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness(store: Store):
        try:
            store.ping()
        except Exception as error:
            logger.exception("Hosting Platform API database readiness check failed")
            raise HTTPException(status_code=503, detail="database_unavailable") from error
        return {"status": "ready"}

    @app.get("/api/v1/health")
    def health(request: Request, response: Response):
        checks = {"database": "ok", "kafka": "ok"}
        try:
            if request.app.state.store is None:
                raise RuntimeError("not configured")
            request.app.state.store.ping()
        except Exception:
            logger.exception("Health check: database unavailable")
            checks["database"] = "unavailable"
        try:
            if not request.app.state.kafka_ready():
                checks["kafka"] = "unavailable"
        except Exception:
            logger.exception("Health check: Kafka unavailable")
            checks["kafka"] = "unavailable"
        healthy = all(value == "ok" for value in checks.values())
        response.status_code = 200 if healthy else 503
        return {"service": "hosting-platform", "status": "ok" if healthy else "degraded", "checks": checks}

    @app.get("/api/v1")
    def version():
        return {"service": "hosting-platform", "version": "v1"}

    # ---- users
    @app.post("/api/v1/users/register", status_code=201)
    def register(payload: RegisterRequest, store: Store):
        try:
            user = store.create_user(payload.username, payload.email, hash_password(payload.password))
        except Conflict as error:
            raise HTTPException(status_code=409, detail="username_or_email_already_registered") from error
        created_at = user["created_at"]
        return {
            "userId": user["user_id"],
            "username": user["username"],
            "email": user["email"],
            "status": user["status"],
            "createdAt": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        }

    @app.get("/api/v1/users")
    def list_users(store: Store):
        """All registered users (no password hashes) with the number of websites each one owns."""
        return {"users": store.list_users()}

    # ---- websites
    @app.post("/api/v1/websites", status_code=202)
    def request_website(payload: WebsiteRequest, request: Request, store: Store):
        owner = store.find_user(payload.email)
        if owner is None:
            raise HTTPException(status_code=404, detail="owner_not_registered")
        if owner["status"] != "ACTIVE":
            raise HTTPException(status_code=403, detail="owner_account_not_active")
        try:
            event, needs_publish = store.request_website(
                owner["user_id"], payload.tenant, payload.domain, payload.name or payload.tenant
            )
        except Conflict as error:
            raise HTTPException(status_code=409, detail="tenant_or_domain_already_exists") from error
        if needs_publish:
            publish_event(request, store, event)
        return {"status": "accepted", "eventId": event["event_id"], "tenant": payload.tenant, "domain": payload.domain}

    @app.get("/api/v1/websites")
    def list_websites(
        store: Store,
        email: Annotated[str | None, Query(description="Only websites of this registered user")] = None,
    ):
        if email is not None:
            try:
                email = _email(email)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        return {"websites": store.list_websites(email)}

    @app.delete("/api/v1/websites", status_code=202)
    def delete_website(
        request: Request,
        store: Store,
        tenant: Annotated[str, Query(description="Tenant (namespace) of the website to delete")],
    ):
        try:
            tenant = _tenant(tenant)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            event, needs_publish = store.request_deletion(tenant)
        except NotFound as error:
            raise HTTPException(status_code=404, detail="website_not_found") from error
        if needs_publish:
            publish_event(request, store, event)
        return {"status": "deletion_requested", "eventId": event["event_id"], "tenant": tenant}

    return app
