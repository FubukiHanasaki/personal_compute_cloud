"""Bound JSON requests before parsing. Author: Fubuki."""

import uuid
from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        state = scope.setdefault("state", {})
        state["request_id"] = uuid.uuid4().hex
        headers = dict(scope["headers"])
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = self.max_bytes + 1
        # A list of arbitrarily tiny chunks can exhaust memory long before the
        # byte limit. Keep buffering cost proportional to actual body bytes.
        body, size = bytearray(), 0
        while declared <= self.max_bytes:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            data = event.get("body", b"")
            size += len(data)
            if size > self.max_bytes:
                break
            body.extend(data)
            if not event.get("more_body"):
                sent = False

                async def replay():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {
                            "type": "http.request",
                            "body": bytes(body),
                            "more_body": False,
                        }
                    return await receive()

                return await self.app(scope, replay, send)
        response = JSONResponse(
            {
                "error": {
                    "code": "request_too_large",
                    "message": "请求体超过上限",
                    "request_id": state["request_id"],
                }
            },
            status_code=413,
            headers={"X-Request-ID": state["request_id"]},
        )
        return await response(scope, receive, send)
