"""Bounded operational controls shared by SynEPD web endpoints."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
import time
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass
class _Window:
    started_at: float
    count: int


class FixedWindowRateLimiter:
    """Small process-local limiter with bounded client state."""

    def __init__(
        self, requests: int = 60, window_seconds: int = 60, clients: int = 4096
    ):
        self.requests = requests
        self.window_seconds = window_seconds
        self.clients = clients
        self._windows: OrderedDict[str, _Window] = OrderedDict()
        self._lock = Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        with self._lock:
            window = self._windows.get(key)
            if window is None or now - window.started_at >= self.window_seconds:
                window = _Window(now, 0)
            window.count += 1
            self._windows[key] = window
            self._windows.move_to_end(key)
            while len(self._windows) > self.clients:
                self._windows.popitem(last=False)
            remaining = max(0, self.requests - window.count)
            return window.count <= self.requests, remaining

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


class RateLimitMiddleware:
    """Pure ASGI rate limiter that does not buffer request/response bodies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: FixedWindowRateLimiter,
        operations: set[tuple[str, str]],
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.operations = operations

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        logical_path = scope.get("path", "")
        if logical_path.startswith("/api/v1/"):
            logical_path = "/api/" + logical_path[len("/api/v1/") :]
        operation = (scope.get("method", "GET"), logical_path)
        if operation not in self.operations:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        host = client[0] if client else "unknown"
        allowed, remaining = self.limiter.check(f"{host}:{logical_path}")
        headers = {
            "X-RateLimit-Limit": str(self.limiter.requests),
            "X-RateLimit-Remaining": str(remaining),
        }
        if not allowed:
            headers["Retry-After"] = str(self.limiter.window_seconds)
            response = JSONResponse(
                status_code=429,
                headers=headers,
                content={
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "Too many requests; retry after the current window.",
                },
            )
            await response(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.update(headers)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestSizeLimitMiddleware:
    """Reject oversized request bodies before JSON or chemistry parsing."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        maximum_bytes: int,
        paths: set[str],
    ) -> None:
        self.app = app
        self.maximum_bytes = maximum_bytes
        self.paths = paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if path.startswith("/api/v1/"):
            path = "/api/" + path[len("/api/v1/") :]
        limited = (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and path in self.paths
        )
        if not limited:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", ()))
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            content_length = 0
        if content_length > self.maximum_bytes:
            await self._reject(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.maximum_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "code": "REQUEST_TOO_LARGE",
                "message": f"Request body exceeds {self.maximum_bytes} bytes.",
            },
        )
        await response(scope, receive, send)
