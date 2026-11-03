"""HTTPS identity/queue persistence against an isolated Compose deployment. FubukiHanasaki."""

import argparse
import http.cookiejar
import json
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["seed", "verify"])
    parser.add_argument("--state", default="tmp/compose-qa/state.json")
    args = parser.parse_args()
    path = Path(args.state)
    state = json.loads(path.read_text())
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(
            context=ssl.create_default_context(cafile="tmp/compose-qa/root.pem")
        ),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        urllib.request.ProxyHandler({}),
    )

    def api(route, body=None, headers=None):
        request = urllib.request.Request(
            "https://localhost" + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        with opener.open(request, timeout=10) as response:
            return json.load(response)

    until = time.monotonic() + 60
    while True:
        try:
            assert api("/healthz")["status"] == "ok"
            break
        except (OSError, urllib.error.URLError):
            if time.monotonic() > until:
                raise
            time.sleep(0.5)
    if args.phase == "seed":
        login = api(
            "/api/v1/auth/login",
            {"username": "Fubuki", "password": state["owner_password"]},
        )
        csrf = {"X-CSRF-Token": login["csrf_token"]}
        api(
            "/api/v1/admin/templates",
            {
                "id": "compose-stats",
                "name": "Compose smoke",
                "kind": "compute.stats",
                "worker": "compute.stats",
                "public": True,
            },
            csrf,
        )
        api(
            "/api/v1/admin/users",
            {"username": "compose-user", "password": state["user_password"]},
            csrf,
        )
        login = api(
            "/api/v1/auth/login",
            {"username": "compose-user", "password": state["user_password"]},
        )
        csrf = {"X-CSRF-Token": login["csrf_token"]}
        state["key"] = api(
            "/api/v1/keys",
            {"scopes": ["jobs:read", "jobs:write"], "expires_seconds": 3600},
            csrf,
        )["key"]
        state["job_id"] = api(
            "/api/v1/jobs",
            {
                "task_type": "compute.stats",
                "template_id": "compose-stats",
                "input": {"values": [1, 2, 3]},
            },
            {"Authorization": "Bearer " + state["key"]},
        )["job_id"]
        path.write_text(json.dumps(state))
        path.chmod(0o600)
    else:
        result = api(
            "/api/v1/jobs/" + state["job_id"],
            headers={"Authorization": "Bearer " + state["key"]},
        )
        assert result["status"] == "queued"  # no fictitious Agent or GPU
        try:
            api("/api/v1/jobs/" + state["job_id"])
        except urllib.error.HTTPError as error:
            assert error.code == 401
            assert json.load(error)["error"]["request_id"]
        else:
            raise AssertionError("Anonymous access must fail")
    print(
        "Compose "
        + args.phase
        + " passed (verified HTTPS, persisted queue/auth; no Agent execution claim)."
    )


if __name__ == "__main__":
    main()
