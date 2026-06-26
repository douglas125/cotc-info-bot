from __future__ import annotations

from sync import fetch


def test_identity_encoding_http_replaces_compression_header(monkeypatch) -> None:
    seen: dict = {}

    def fake_request(self, uri, method="GET", body=None, headers=None,
                     redirections=None, connection_type=None):
        seen["headers"] = headers
        return {"status": "200"}, b"{}"

    monkeypatch.setattr(fetch.httplib2.Http, "request", fake_request)

    http = fetch._IdentityEncodingHttp()
    http.request(
        "https://sheets.googleapis.com/test",
        headers={"Accept-Encoding": "gzip, deflate", "x-test": "ok"},
    )

    assert seen["headers"]["accept-encoding"] == "identity"
    assert "Accept-Encoding" not in seen["headers"]
    assert seen["headers"]["x-test"] == "ok"


def test_build_service_uses_identity_encoding_http(monkeypatch) -> None:
    seen: dict = {}

    def fake_build(service_name, version, **kwargs):
        seen["service_name"] = service_name
        seen["version"] = version
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(fetch, "build", fake_build)

    svc = fetch._build_service("api-key")

    assert svc is not None
    assert seen["service_name"] == "sheets"
    assert seen["version"] == "v4"
    assert seen["developerKey"] == "api-key"
    assert seen["cache_discovery"] is False
    assert isinstance(seen["http"], fetch._IdentityEncodingHttp)
