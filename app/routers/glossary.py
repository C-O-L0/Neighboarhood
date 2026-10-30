"""The glossary: plain-language meanings of the terms the pages use. Static, no database call."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.templating import templates

router = APIRouter()


@router.get("/glossary", response_class=HTMLResponse)
def glossary(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "glossary.html")
