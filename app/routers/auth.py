"""Register, sign in, sign out and the account page. CSRF is enforced for every POST by app.services.guards."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.repos.dto import Duplicate
from app.services.auth import (
    LoginForm,
    RegisterForm,
    error_messages,
    hash_password,
    sign_in,
    verify_password,
)
from app.services.repos import call_repo
from app.templating import templates

router = APIRouter()

BAD_LOGIN = "Email or password is incorrect."
THROTTLED = "Too many failed sign-in attempts. Try again in 15 minutes."
TAKEN = "An account with that email already exists. Sign in instead."


def _page(request: Request, template: str, status: int = 200, **context):
    return templates.TemplateResponse(request, template, context, status_code=status)


def _redirect(request: Request, path: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?db={request.state.db_mode}", status_code=303)


def _client_ip(request: Request) -> str:
    # No proxy sits in front of the app, so forwarded headers would only be attacker-controlled.
    return request.client.host if request.client else "unknown"


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    return _page(request, "register.html", errors=[], f={})


@router.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    display_name: str = Form(""),
):
    echo = {"email": email[:254], "display_name": display_name[:60]}
    try:
        form = RegisterForm.model_validate({"email": email, "password": password, "display_name": display_name})
    except ValidationError as exc:
        return _page(request, "register.html", 422, errors=error_messages(exc), f=echo)
    result = call_repo(
        request, "accounts", "create", form.email, hash_password(form.password), form.display_name, label="register"
    )
    if isinstance(result, Duplicate):
        return _page(request, "register.html", 409, errors=[TAKEN], f=echo)
    return _redirect(request, "/login")


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return _page(request, "login.html", errors=[], f={})


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(""), password: str = Form("")):
    echo = {"email": email[:254]}
    try:
        form = LoginForm.model_validate({"email": email, "password": password})
    except ValidationError as exc:
        return _page(request, "login.html", 422, errors=error_messages(exc), f=echo)
    throttle = request.app.state.throttle
    key = (form.email, _client_ip(request))
    if throttle.blocked(key):
        return _page(request, "login.html", 429, errors=[THROTTLED], f=echo)
    account = call_repo(request, "accounts", "by_email", form.email, label="login")
    if not verify_password(account.password_hash if account else None, form.password):
        throttle.record_failure(key)
        return _page(request, "login.html", 401, errors=[BAD_LOGIN], f=echo)
    throttle.clear(key)
    call_repo(request, "accounts", "touch", account.buyer_id, label="login.touch")
    sign_in(request.session, request.state.db_mode, account.buyer_id)
    return _redirect(request, "/account")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(f"/?db={request.state.db_mode}", status_code=303)


@router.get("/account", response_class=HTMLResponse)
def account(request: Request):
    if request.state.account is None:
        return _redirect(request, "/login")
    return _page(request, "account.html", account=request.state.account)
