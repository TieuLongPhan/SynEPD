import asyncio

from synepd.web.operations import (
    FixedWindowRateLimiter,
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
)


def test_rate_limiter_resets_and_bounds_clients():
    limiter = FixedWindowRateLimiter(requests=2, window_seconds=10, clients=2)
    assert limiter.check("a", now=0) == (True, 1)
    assert limiter.check("a", now=1) == (True, 0)
    assert limiter.check("a", now=2) == (False, 0)
    assert limiter.check("a", now=11) == (True, 1)
    limiter.check("b", now=11)
    limiter.check("c", now=11)
    assert len(limiter._windows) == 2


def test_request_size_limit_rejects_chunked_body_with_stable_error():
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    middleware = RequestSizeLimitMiddleware(
        downstream,
        maximum_bytes=4,
        paths={"/api/query-epd"},
    )
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"de", "more_body": False},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/query-epd",
                "headers": [],
            },
            receive,
            send,
        )
    )

    assert called is False
    assert sent[0]["status"] == 413
    assert b"REQUEST_TOO_LARGE" in sent[1]["body"]


def test_rate_limit_middleware_normalizes_v1_paths():
    limiter = FixedWindowRateLimiter(requests=1, window_seconds=10)
    limiter.check("test:/api/query-epd")
    called = False
    sent = []

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = RateLimitMiddleware(
        downstream,
        limiter=limiter,
        operations={("POST", "/api/query-epd")},
    )
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/query-epd",
                "client": ("test", 1234),
                "headers": [],
            },
            receive,
            send,
        )
    )

    assert called is False
    assert sent[0]["status"] == 429
    assert (b"retry-after", b"10") in sent[0]["headers"]
