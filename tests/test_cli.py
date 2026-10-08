import json

import httpx

from group_payments import NetworkError, cli, webhook

EV = [{"id": f"e{i}", "type": "payment.succeeded", "createdAt": f"2030-01-01T00:00:0{i}Z", "data": {"payment": {"id": "p"}}}
      for i in range(3)]


class FakeEvents:
    def __init__(self, pages):
        self.pages = pages

    def list(self, **_):
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return {"items": page}


class FakeClient:
    def __init__(self, pages):
        self.events = FakeEvents(pages)


def test_login_writes_private_config(tmp_path, monkeypatch):
    cfg = tmp_path / "gp.json"
    monkeypatch.setenv("GP_CONFIG", str(cfg))
    assert cli.main(["login", "--key", "gp_test_abcdefgh_" + "x" * 32, "--base-url", "https://api.test/v1"]) == 0
    saved = json.loads(cfg.read_text())
    assert saved["key"].startswith("gp_test_") and saved["baseUrl"] == "https://api.test/v1"


def test_poll_once_primes_then_returns_new_events_oldest_first():
    seen: dict[str, None] = {}
    c = FakeClient([[EV[0]], [EV[2], EV[1], EV[0]]])
    assert cli.poll_once(c, seen) == [EV[0]]
    assert [e["id"] for e in cli.poll_once(c, seen)] == ["e1", "e2"]


def test_listen_forwards_signed_payload(capsys):
    got = []

    def handler(req):
        got.append(req)
        return httpx.Response(200)

    c = FakeClient([[EV[0]], [EV[1], EV[0]]])
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert cli.listen(c, "http://localhost:3000/hook", 2.0, "whsec_local", sleep=lambda s: None, forever=False, http=http) == 0
    assert len(got) == 1 and got[0].headers["GP-Event-Id"] == "e1"
    body = webhook.verify(got[0].content, dict(got[0].headers), "whsec_local")
    assert body == {"event": "payment.succeeded", "eventId": "e1", "createdAt": EV[1]["createdAt"], "payment": {"id": "p"}}
    assert "whsec_local" in capsys.readouterr().out


def test_listen_survives_api_errors(capsys):
    c = FakeClient([[EV[0]], NetworkError("down", code="network_error")])
    assert cli.listen(c, "http://localhost:3000/hook", 2.0, "s", sleep=lambda s: None, forever=False) == 0
    assert "network_error" in capsys.readouterr().err
