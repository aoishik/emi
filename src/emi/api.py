import asyncio
from fastapi import FastAPI
import traceback
from contextlib import asynccontextmanager
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from fastapi import Depends, Header, HTTPException, Request, Response, status
from starlette.routing import Match

from emi.payloads import ShipPayload, FulfillOrderPayload, FulfillFullfilledPayload, ReviewAcceptPayload, ReviewRejectPayload, VotingCompletePayload
from emi.slack import slack_relay, SlackRelay
from emi.settings import settings, Settings


async def bot_heartbeat_task(settings: Settings, slack_relay: SlackRelay) -> None:
	"""Background task that sends a heartbeat message every 24 hrs to the logging channel."""
	if not settings.logging_channel_id:
		return

	while True:
		try:
			await asyncio.sleep(86400)  # 24 hrs
			resp = await slack_relay.app.client.chat_postMessage(
				channel=settings.logging_channel_id,
				text="Bot is Online!",
			)
			if not resp.get("ok"):
				print(f"Failed to send heartbeat: {resp}")
		except Exception as e:
			print(f"Heartbeat task error (non-fatal): {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
	"""Manage app startup and shutdown, including background tasks."""
	settings = Settings()
	task = None
	if settings.logging_channel_id:
		task = asyncio.create_task(bot_heartbeat_task(settings, slack_relay))
	yield
	if task:
		task.cancel()
		try:
			await task
		except asyncio.CancelledError:
			pass



app = FastAPI(title=settings.app_name, lifespan=lifespan)

class CustomMessagePayload(BaseModel):
    target_id: str = Field(min_length=1, description="Slack user ID or channel ID")
    message: str = Field(min_length=1, max_length=4000)

class BlockKitPayload(BaseModel):
    target_id: str = Field(min_length=1, description="Slack user ID or channel ID")
    title: str = Field(min_length=1, max_length=200)
    blocks: list[dict] = Field(min_length=1)


def _truncate_message(value: str, limit: int = 3000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...(truncated)"


def _format_request_snapshot(request: Request, body: bytes | None) -> str:
    sensitive_headers = {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-slack-signature",
    }
    redacted = "[redacted]"
    headers_lines = []
    for key, value in request.headers.items():
        if key.lower() in sensitive_headers:
            headers_lines.append(f"{key}: {redacted}")
        else:
            headers_lines.append(f"{key}: {value}")
    headers_text = "\n".join(headers_lines)
    body_text = ""
    if body:
        body_text = body.decode("utf-8", errors="replace")
    return "\n\n".join(
        [
            f"Request headers:\n{headers_text or '(none)'}",
            f"Request body:\n{body_text or '(empty)'}",
        ]
    )


def _format_response_snapshot(body_text: str) -> str:
    return f"Response body:\n{body_text or '(empty)'}"


def _format_traceback(trace: str) -> str:
    return f"Traceback:\n{trace}"


def _join_detail(*parts: str | None) -> str | None:
    items = [part for part in parts if part]
    if not items:
        return None
    return "\n\n".join(items)


async def _extract_response_body(response: Response) -> tuple[Response, str | None]:
    body = getattr(response, "body", None)
    if isinstance(body, (bytes, bytearray)) and body:
        return response, body.decode("utf-8", errors="replace")
    if isinstance(body, str) and body:
        return response, body
    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is None:
        return response, None
    chunks: list[bytes] = []
    async for chunk in body_iterator:
        if isinstance(chunk, (bytes, bytearray)):
            chunks.append(bytes(chunk))
        else:
            chunks.append(str(chunk).encode("utf-8"))
    if not chunks:
        return response, None
    body_bytes = b"".join(chunks)
    headers = dict(response.headers)
    headers.pop("content-length", None)
    headers.pop("transfer-encoding", None)
    return (
        Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
            background=response.background,
        ),
        body_bytes.decode("utf-8", errors="replace"),
    )


def _is_known_route(request: Request) -> bool:
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match is Match.FULL:
            return True
    return False


def verify_bearer_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    if not settings.auth_bearer_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server missing AUTH_BEARER_TOKEN; configure it in the environment",
        )
    expected = f"Bearer {settings.auth_bearer_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.middleware("http")
async def error_logging_middleware(request: Request, call_next):
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    is_known_route = _is_known_route(request)
    should_log = is_known_route and request.url.path != "/slack/events"
    try:
        response = await call_next(request)
    except Exception as exc:
        if should_log:
            request_body = None
            try:
                request_body = await request.body()
            except Exception:
                request_body = None
            await slack_relay.log_error(
                _truncate_message(
                    f":warning: {request.method} {path} -> 500 {type(exc).__name__}: {exc}"
                ),
                detail=_join_detail(
                    _format_request_snapshot(request, request_body),
                    _format_traceback(traceback.format_exc()),
                ),
            )
        raise
    if should_log and response.status_code >= 400:
        request_body = None
        try:
            request_body = await request.body()
        except Exception:
            request_body = None
        response, response_body = await _extract_response_body(response)
        await slack_relay.log_error(
            _truncate_message(
                f":warning: {request.method} {path} -> {response.status_code}"
            ),
            detail=_join_detail(
                _format_request_snapshot(request, request_body),
                _format_response_snapshot(response_body or ""),
            ),
        )
    return response


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/slack/events", status_code=200)
async def slack_events(request: Request):
    # Slack signature verification is enforced by the Bolt handler when a signing secret is set.
    if not settings.slack_signing_secret:
        is_slack_request = bool(
            request.headers.get("X-Slack-Signature")
            and request.headers.get("X-Slack-Request-Timestamp")
        )
        await slack_relay.log_error(
            ":warning: /slack/events received without SLACK_SIGNING_SECRET; cannot verify signature."
        )
        if is_slack_request:
            # Acknowledge Slack to avoid retries even when we cannot verify.
            return Response(status_code=status.HTTP_200_OK)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server missing SLACK_SIGNING_SECRET; configure it in the environment",
        )
    try:
        response = await slack_relay.handler.handle(request)
        request_body = None
        try:
            request_body = await request.body()
        except Exception:
            request_body = None
        response, response_body = await _extract_response_body(response)
        if response.status_code >= 400:
            await slack_relay.log_error(
                _truncate_message(
                    f":warning: {request.method} /slack/events -> {response.status_code}"
                ),
                detail=_join_detail(
                    _format_request_snapshot(request, request_body),
                    _format_response_snapshot(response_body or ""),
                ),
            )
        return response
    except Exception as exc:
        request_body = None
        try:
            request_body = await request.body()
        except Exception:
            request_body = None
        await slack_relay.log_error(
            f":warning: /slack/events handler error: {exc}",
            detail=_join_detail(
                _format_request_snapshot(request, request_body),
                _format_traceback(traceback.format_exc()),
            ),
        )
        # Always acknowledge to prevent Slack retries from leaking errors to callers.
        return Response(status_code=status.HTTP_200_OK)


@app.post("/ship")
async def ship_project(
    payload: ShipPayload,
    _: None = Depends(verify_bearer_token),
) -> dict[str, Any]:
    """Handle a project submission (ship) from a user.

    Sends a public message to the configured ship channel and DM's the submitting user.
    """
    ship_channel = settings.ship_channel_id
    if not ship_channel:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Server missing SHIP_CHANNEL_ID; configure it in the environment",
        )

    # Public channel notification: ping the user and mention the project (bold project name)
    #public_message = f"<@{payload.user_id}> Your *{payload.project_name}* has been submitted for review."
    blocks = [
    {
    "type": "card",
    "title": {
    "type": "mrkdwn",
    "text": "New Submisson",
    "verbatim": False
    },
    "subtitle": {
    "type": "mrkdwn",
    "text": f"Submitted by <@{payload.user_id}>",
    "verbatim": False
    },
    "body": {
    "type": "mrkdwn",
    "text": f"{payload.project_name}",
    "verbatim": False
    }
    },
    {
    "type": "context",
    "elements": [
    {
    "type": "mrkdwn",
    "text": f"<{payload.project_link}|View Project> · {datetime.now(ZoneInfo("Asia/Kolkata")).strftime('%d-%m-%Y %H:%M:%S %Z')}" #03/07/2026 13:09 IST"
    }
    ]
    }
    ]
    public_resp = await slack_relay.app.client.chat_postMessage(
        channel=ship_channel,
        blocks=blocks,
    )

    # Direct message to the submitter
    dm_resp = await slack_relay.send_dm(payload.user_id, payload.project_name, payload.project_link)


    return {
        "public": {"ok": bool(public_resp["ok"]), "channel": ship_channel, "ts": public_resp.get("ts")},
        "dm": dm_resp.model_dump(),
    }


@app.post("/review-accept")
async def review_accept(
    payload: ReviewAcceptPayload,
    _: None = Depends(verify_bearer_token),
) -> dict[str, Any]:
    """Handle a positive review for a submitted project.

    Posts a review message to the ship channel with custom reviewer profile (name/avatar)
    and sends DM notification to the project submitter.
    """
    review_response = await slack_relay.post_review_accept(
        user_id=payload.user_id,
        project_name=payload.project_name,
        project_link=payload.project_link,
        reviewer_id=payload.reviewer_id,
        feedback=payload.feedback,
    )
    return {"ok": review_response["ok"], "channel": review_response["channel"], "ts": review_response.get("ts")}


@app.post("/review-reject")
async def review_reject(
    payload: ReviewRejectPayload,
    _: None = Depends(verify_bearer_token),
) -> dict[str, Any]:
    """Handle a negative review for a submitted project.

    Posts a review message to the ship channel with custom reviewer profile (name/avatar)
    and sends DM notification to the project submitter.
    """
    review_response = await slack_relay.post_review_reject(
        user_id=payload.user_id,
        project_name=payload.project_name,
        project_link=payload.project_link,
        reviewer_id=payload.reviewer_id,
        feedback=payload.feedback,
    )
    return {"ok": review_response["ok"], "channel": review_response["channel"], "ts": review_response.get("ts")}


@app.post("/fulfill_pending")
async def fulfill_pending(
    payload: FulfillOrderPayload,
    _: None = Depends(verify_bearer_token),
) -> dict[str, Any]:
    response = await slack_relay.send_fulfill_pending_dm(payload)
    return {"ok": response.ok, "channel": response.channel, "ts": response.ts}


@app.post("/fulfill_approved")
async def fulfill_approved(
    payload: FulfillOrderPayload,
    _: None = Depends(verify_bearer_token),
) -> dict[str, Any]:
    response = await slack_relay.send_fulfill_approved_dm(payload)
    return {"ok": response.ok, "channel": response.channel, "ts": response.ts}


@app.post("/fulfill_reject")
async def fulfill_reject(
    payload: FulfillOrderPayload,
    _: None = Depends(verify_bearer_token),
) -> dict[str, Any]:
    response = await slack_relay.send_fulfill_reject_dm(payload)
    return {"ok": response.ok, "channel": response.channel, "ts": response.ts}


@app.post("/fulfill_fullfilled")
async def fulfill_fullfilled(
    payload: FulfillFullfilledPayload,
    _: None = Depends(verify_bearer_token),
) -> dict[str, Any]:
    response = await slack_relay.send_fulfill_fullfilled_dm(payload)
    return {"ok": response.ok, "channel": response.channel, "ts": response.ts}


@app.post("/custom")
async def custom_message(
    payload: CustomMessagePayload,
    _: None = Depends(verify_bearer_token),
) -> dict[str, Any]:
    response = await slack_relay.send_custom_message(payload.target_id, payload.message)
    return {"ok": response.ok, "channel": response.channel, "ts": response.ts}

@app.post("/blockkit")
async def block_kit_msg(
    payload: BlockKitPayload,
    _: None = Depends(verify_bearer_token),
) -> dict[str, Any]:
    response = await slack_relay.send_block_kit(payload.target_id, payload.title, payload.blocks)
    return {"ok": response.ok, "channel": response.channel, "ts": response.ts}


@app.post("/voting-complete")
async def voting_complete(
    payload: VotingCompletePayload,
    _: None = Depends(verify_bearer_token),
) -> dict[str, Any]:
    """Handle voting completion for a project.

    Posts a voting complete message to the ship channel with custom reviewer profile (name/avatar)
    and sends DM notification to the project submitter with reward information.
    """
    voting_response = await slack_relay.post_voting_complete(
        user_id=payload.user_id,
        project_name=payload.project_name,
        project_link=payload.project_link,
        vote=payload.vote,
        feedback=payload.feedback,
        currencies=payload.currencies,
    )
    return {"ok": voting_response["ok"], "channel": voting_response["channel"], "ts": voting_response.get("ts")}
