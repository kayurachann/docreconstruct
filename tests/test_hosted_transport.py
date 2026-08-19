from __future__ import annotations

import io
from email.message import Message
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

import pytest

from docreconstruct.providers import ProviderHTTPError
from docreconstruct.providers import _hosted as hosted


class _StreamingResponse:
    def __init__(
        self,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = io.BytesIO(body)
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._body.read(size)

    def __enter__(self) -> _StreamingResponse:
        return self

    def __exit__(self, *_: Any) -> None:
        self.closed = True


class _Opener:
    def __init__(self, response: _StreamingResponse) -> None:
        self.response = response
        self.requests: list[tuple[urllib_request.Request, float]] = []

    def open(self, request: urllib_request.Request, *, timeout: float) -> _StreamingResponse:
        self.requests.append((request, timeout))
        return self.response


def test_stdlib_transport_reads_success_response_in_bounded_chunks_and_keeps_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _StreamingResponse(b"abcdefgh")
    opener = _Opener(response)
    monkeypatch.setattr(hosted, "_HOSTED_RESPONSE_READ_CHUNK_BYTES", 3)
    monkeypatch.setattr(hosted.urllib_request, "build_opener", lambda *_: opener)

    result = hosted.stdlib_http_transport(
        method="POST",
        url="https://api.example.test/ocr",
        headers={"Authorization": "Bearer secret"},
        body=b"request",
        timeout=2.75,
    )

    assert result.body == b"abcdefgh"
    assert response.read_sizes == [3, 3, 3, 3]
    assert response.closed is True
    assert opener.requests[0][1] == 2.75


def test_stdlib_transport_rejects_streamed_response_over_safety_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _StreamingResponse(b"abcdef")
    opener = _Opener(response)
    monkeypatch.setattr(hosted, "MAX_HOSTED_RESPONSE_BYTES", 5)
    monkeypatch.setattr(hosted.urllib_request, "build_opener", lambda *_: opener)

    with pytest.raises(ProviderHTTPError, match="5-byte safety limit"):
        hosted.stdlib_http_transport(
            method="GET",
            url="https://api.example.test/result",
            headers={},
            body=None,
            timeout=1.0,
        )

    assert response.read_sizes == [6]
    assert response.closed is True


def test_stdlib_transport_rejects_oversized_content_length_before_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _StreamingResponse(b"ignored", headers={"Content-Length": "6"})
    opener = _Opener(response)
    monkeypatch.setattr(hosted, "MAX_HOSTED_RESPONSE_BYTES", 5)
    monkeypatch.setattr(hosted.urllib_request, "build_opener", lambda *_: opener)

    with pytest.raises(ProviderHTTPError, match="5-byte safety limit"):
        hosted.stdlib_http_transport(
            method="GET",
            url="https://api.example.test/result",
            headers={},
            body=None,
            timeout=1.0,
        )

    assert response.read_sizes == []
    assert response.closed is True


def test_stdlib_transport_never_follows_redirect_with_secret_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_handlers: list[Any] = []
    opened_requests: list[urllib_request.Request] = []

    class RedirectOpener:
        def open(self, request: urllib_request.Request, *, timeout: float) -> Any:
            del timeout
            opened_requests.append(request)
            redirect = captured_handlers[0].redirect_request(
                request,
                io.BytesIO(),
                307,
                "Temporary Redirect",
                {},
                "https://attacker.example/steal",
            )
            assert redirect is None
            headers = Message()
            headers["Location"] = "https://attacker.example/steal"
            raise urllib_error.HTTPError(
                request.full_url,
                307,
                "Temporary Redirect",
                headers,
                io.BytesIO(b"redirect refused"),
            )

    def build_opener(*handlers: Any) -> RedirectOpener:
        captured_handlers.extend(handlers)
        return RedirectOpener()

    monkeypatch.setattr(hosted.urllib_request, "build_opener", build_opener)
    response = hosted.stdlib_http_transport(
        method="POST",
        url="https://trusted.example/ocr",
        headers={
            "Authorization": "Bearer secret",
            "Ocp-Apim-Subscription-Key": "api-secret",
        },
        body=b"private document",
        timeout=3.0,
    )

    assert response.status == 307
    assert response.headers["Location"] == "https://attacker.example/steal"
    assert len(opened_requests) == 1
    assert opened_requests[0].full_url == "https://trusted.example/ocr"
    assert opened_requests[0].get_header("Authorization") == "Bearer secret"
