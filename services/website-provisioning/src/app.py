import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)
TENANT_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)


class WebsiteRequest(BaseModel):
    tenant: str
    domain: str

    @field_validator("tenant")
    @classmethod
    def tenant_must_be_dns_safe(cls, value):
        if not TENANT_RE.fullmatch(value):
            raise ValueError("Use 3–32 lowercase letters, numbers, and hyphens; start with a letter.")
        return value

    @field_validator("domain")
    @classmethod
    def domain_must_be_a_hostname(cls, value):
        value = value.lower()
        if not DOMAIN_RE.fullmatch(value):
            raise ValueError("Enter a valid domain name, such as shop.example.com.")
        return value


class RequestConflict(Exception):
    """A website name or domain is already used by another request."""


def create_app(ping=None, submit=None, lifespan=None):
    @asynccontextmanager
    async def default_lifespan(_app):
        yield

    app = FastAPI(title="Website Provisioning API", version="1.0", lifespan=lifespan or default_lifespan)
    app.state.ping_database = ping or (lambda: None)
    app.state.submit_website = submit

    @app.get("/health/live")
    def liveness():
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness(request: Request):
        try:
            request.app.state.ping_database()
        except Exception as error:
            logger.exception("Provisioning API database readiness check failed")
            raise HTTPException(status_code=503, detail="database_unavailable") from error
        return {"status": "ready"}

    @app.get("/api/v1")
    def version():
        return {"service": "website-provisioning", "version": "v1"}

    @app.post("/api/v1/websites", status_code=202)
    def request_website(payload: WebsiteRequest, request: Request):
        submitter = request.app.state.submit_website
        if submitter is None:
            raise HTTPException(status_code=503, detail="provisioning_unavailable")
        try:
            event = submitter(payload.tenant, payload.domain)
        except RequestConflict as error:
            raise HTTPException(status_code=409, detail="tenant_or_domain_already_exists") from error
        except Exception as error:
            logger.exception("Website request could not be accepted")
            raise HTTPException(status_code=503, detail="provisioning_unavailable") from error
        return {
            "status": "accepted",
            "eventId": event["event_id"],
            "tenant": payload.tenant,
            "domain": payload.domain,
        }

    return app
