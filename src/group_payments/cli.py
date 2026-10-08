
import argparse
import json
import os
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from group_payments.client import Client
from group_payments.errors import GroupPayError
from group_payments.webhook import sign


def _config_path() -> Path:
    return Path(os.environ.get("GP_CONFIG") or Path.home() / ".config" / "gp" / "config.json")


def _client() -> Client:
    try:
        cfg = json.loads(_config_path().read_text())
    except (OSError, ValueError):
        sys.exit("Run `gp login --key gp_test_...` first")
    return Client(cfg["key"], base_url=cfg.get("baseUrl"))


def payload(ev: dict[str, Any]) -> bytes:
    body = {"event": ev["type"], "eventId": ev["id"], "createdAt": ev["createdAt"], **ev["data"]}
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()


def poll_once(client: Any, seen: dict[str, None]) -> list[dict[str, Any]]:
    items = client.events.list(limit=100)["items"]
    fresh = [e for e in items if e["id"] not in seen]
    for e in reversed(items):
        seen.setdefault(e["id"], None)
    while len(seen) > 1000:
        seen.pop(next(iter(seen)))
    return list(reversed(fresh))


def listen(client: Any, url: str, interval: float, secret: str, *, sleep: Callable[[float], Any] = time.sleep,
           forever: bool = True, http: httpx.Client | None = None) -> int:
    print(f"Пересылка событий на {url}\nСекрет подписи: {secret}", flush=True)
    http = http or httpx.Client(timeout=10)
    seen: dict[str, None] = {}
    poll_once(client, seen)
    while True:
        sleep(interval)
        try:
            fresh = poll_once(client, seen)
        except GroupPayError as e:
            print(f"Ошибка {e.code}: {e}", file=sys.stderr, flush=True)
            fresh = []
        for ev in fresh:
            body = payload(ev)
            headers = {"Content-Type": "application/json", "GP-Event": ev["type"], "GP-Event-Id": ev["id"],
                       **sign(body, secret)}
            try:
                status = str(http.post(url, content=body, headers=headers).status_code)
            except httpx.HTTPError as e:
                status = f"ошибка: {e}"
            print(f"{ev['createdAt']}  {ev['type']:<24} {ev['id']}  {status}", flush=True)
        if not forever:
            return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gp", description="Group Pay CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("login", help="сохранить ключ API")
    p.add_argument("--key", required=True)
    p.add_argument("--base-url")
    p = sub.add_parser("trigger", help="запустить сценарий песочницы")
    p.add_argument("event")
    p = sub.add_parser("listen", help="пересылать события на локальный адрес")
    p.add_argument("--forward-to", required=True)
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--secret")
    p = sub.add_parser("events")
    es = p.add_subparsers(dest="sub", required=True)
    r = es.add_parser("resend", help="отправить событие повторно")
    r.add_argument("id")
    r.add_argument("--endpoint")
    args = ap.parse_args(argv)

    if args.cmd == "login":
        Client(args.key, base_url=args.base_url)
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"key": args.key, "baseUrl": args.base_url}))
        path.chmod(0o600)
        print(f"Ключ {args.key[:16]}… сохранён в {path}")
        return 0
    client = _client()
    try:
        if args.cmd == "trigger":
            print(json.dumps(client.test.trigger(args.event), ensure_ascii=False, indent=2))
        elif args.cmd == "events":
            print(json.dumps(client.events.resend(args.id, args.endpoint), ensure_ascii=False, indent=2))
        else:
            return listen(client, args.forward_to, args.interval, args.secret or "whsec_" + secrets.token_hex(16))
    except GroupPayError as e:
        print(f"Ошибка {e.code}: {e} (request {e.request_id})", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0
