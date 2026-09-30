"""Development-only OpenID Connect provider, so SSO can be demonstrated and tested locally.

    python -m app.devtools.dev_oidc_provider        # http://localhost:9000

It implements just enough of OIDC for OpsFlow's flow: discovery, an /authorize page where you
type the email you want to "be", /token (authorization code + PKCE S256, client secret), and /jwks
with an RS256 key generated at start-up. It does NOT authenticate anyone — never expose it.

Environment:
  DEV_OIDC_PUBLIC_URL    issuer / browser-facing URL   (default http://localhost:9000)
  DEV_OIDC_INTERNAL_URL  URL the OpsFlow backend uses for /token and /jwks (default: public URL)
  DEV_OIDC_CLIENT_ID     (default opsflow-dev)
  DEV_OIDC_CLIENT_SECRET (default opsflow-dev-secret)
"""

import base64
import hashlib
import html
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


def create_app(
    public_url: str = "http://localhost:9000",
    client_id: str = "opsflow-dev",
    client_secret: str = "opsflow-dev-secret",
    unverified_emails: tuple[str, ...] = (),
    internal_url: str | None = None,
) -> FastAPI:
    public_url = public_url.rstrip("/")
    # Back-channel endpoints (token, JWKS) may live on an internal hostname, e.g. inside Docker.
    backchannel = (internal_url or public_url).rstrip("/")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = secrets.token_hex(8)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    public_jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    codes: dict[str, dict[str, Any]] = {}
    app = FastAPI(title="OpsFlow dev OIDC provider", docs_url=None, redoc_url=None)
    app.state.signing_key = key
    app.state.kid = kid

    @app.get("/.well-known/openid-configuration")
    def discovery() -> dict[str, Any]:
        return {
            "issuer": public_url,
            "authorization_endpoint": f"{public_url}/authorize",
            "token_endpoint": f"{backchannel}/token",
            "jwks_uri": f"{backchannel}/jwks",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
            "scopes_supported": ["openid", "email", "profile"],
        }

    @app.get("/jwks")
    def jwks() -> dict[str, Any]:
        return {"keys": [public_jwk]}

    @app.get("/authorize", response_class=HTMLResponse)
    def authorize_form(request: Request) -> str:
        p = request.query_params
        if p.get("client_id") != client_id or p.get("response_type") != "code":
            raise HTTPException(400, "invalid client or response_type")
        if p.get("code_challenge_method") != "S256" or not p.get("code_challenge"):
            raise HTTPException(400, "PKCE S256 required")
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in p.items()
        )
        return f"""<!doctype html><html><head><title>Dev SSO</title><meta name="viewport" content="width=device-width">
<style>body{{font-family:system-ui;background:#f4f5f8;display:grid;place-items:center;height:100vh;margin:0}}
form{{background:#fff;padding:24px;border-radius:10px;border:1px solid #dde1e8;width:340px}}
input[type=email]{{width:100%;padding:8px;margin:8px 0 14px;box-sizing:border-box}}
button{{background:#151a25;color:#fff;border:0;padding:9px 14px;border-radius:6px;width:100%}}
small{{color:#8e97a8}}</style></head><body>
<form method="post" action="/authorize"><h3>Development identity provider</h3>
<small>No password — this stand-in IdP signs in whoever you type. Development only.</small>
<label for="email"><div style="margin-top:12px">Email</div></label>
<input id="email" name="email" type="email" required value="omar@opsflow.dev">{hidden}
<button type="submit">Continue</button></form></body></html>"""

    @app.post("/authorize")
    def authorize_submit(
        email: str = Form(...),
        client_id_: str = Form(..., alias="client_id"),
        redirect_uri: str = Form(...),
        state: str = Form(...),
        nonce: str = Form(""),
        code_challenge: str = Form(...),
        scope: str = Form("openid"),
    ) -> RedirectResponse:
        if client_id_ != client_id:
            raise HTTPException(400, "invalid client")
        code = secrets.token_urlsafe(24)
        codes[code] = {
            "email": email.strip().lower(),
            "redirect_uri": redirect_uri,
            "nonce": nonce,
            "challenge": code_challenge,
            "expires": time.time() + 120,
        }
        return RedirectResponse(
            f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", status_code=302
        )

    @app.post("/token")
    def token(
        grant_type: str = Form(...),
        code: str = Form(...),
        redirect_uri: str = Form(...),
        client_id_: str = Form(..., alias="client_id"),
        client_secret_: str = Form("", alias="client_secret"),
        code_verifier: str = Form(...),
    ) -> JSONResponse:
        def error(reason: str) -> JSONResponse:
            return JSONResponse({"error": reason}, status_code=400)

        if grant_type != "authorization_code":
            return error("unsupported_grant_type")
        if client_id_ != client_id or not secrets.compare_digest(client_secret_, client_secret):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        entry = codes.pop(code, None)  # single use
        if not entry or entry["expires"] < time.time() or entry["redirect_uri"] != redirect_uri:
            return error("invalid_grant")
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
        )
        if not secrets.compare_digest(digest, entry["challenge"]):
            return error("invalid_grant")
        now = int(time.time())
        email = entry["email"]
        claims = {
            "iss": public_url,
            "aud": client_id,
            "sub": hashlib.sha256(email.encode()).hexdigest()[:24],
            "email": email,
            "email_verified": email not in unverified_emails,
            "name": email.split("@")[0].replace(".", " ").title(),
            "nonce": entry["nonce"],
            "iat": now,
            "exp": now + 300,
        }
        id_token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})
        return JSONResponse(
            {
                "access_token": secrets.token_urlsafe(24),
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": id_token,
            }
        )

    return app


def main() -> None:
    import uvicorn

    if os.environ.get("OPSFLOW_ENV", "").lower() == "production":
        raise SystemExit("The development OIDC provider must never run in production")
    public = os.environ.get("DEV_OIDC_PUBLIC_URL", "http://localhost:9000")
    app = create_app(
        public,
        os.environ.get("DEV_OIDC_CLIENT_ID", "opsflow-dev"),
        os.environ.get("DEV_OIDC_CLIENT_SECRET", "opsflow-dev-secret"),
        internal_url=os.environ.get("DEV_OIDC_INTERNAL_URL"),
    )
    uvicorn.run(
        app,
        host=os.environ.get("DEV_OIDC_HOST", "0.0.0.0"),
        port=int(os.environ.get("DEV_OIDC_PORT", "9000")),
    )


if __name__ == "__main__":
    main()
