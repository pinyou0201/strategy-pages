"""Reconcile published GitHub Pages with the exact dashboard file on master.

A GitHub-managed Pages deploy can fail on transient 503 / OIDC service errors
after Jekyll has successfully built. Recover without modifying strategy state.
"""
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

REPOSITORY = "pinyou0201/strategy-pages"
PUBLIC_URL = "https://pinyou0201.github.io/strategy-pages/"
API_BASE = f"https://api.github.com/repos/{REPOSITORY}"


def api_request(token, path, *, method="GET"):
    request = Request(
        f"{API_BASE}/{path}",
        data=b"" if method == "POST" else None,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "strategy-pages-recovery/1",
        },
    )
    with urlopen(request, timeout=20) as response:
        return response.status, response.read(1024 * 1024)


def source_head(token):
    status, data = api_request(token, "git/ref/heads/master")
    if status != 200:
        raise RuntimeError(f"Cannot read current master ref (HTTP {status})")
    return json.loads(data)["object"]["sha"]


def public_page(digest):
    query = urlencode({"deployment": digest})
    url = f"{PUBLIC_URL}?{query}"
    request = Request(url, headers={
        "Cache-Control": "no-cache",
        "User-Agent": "strategy-pages-recovery/1",
    })
    with urlopen(request, timeout=20) as response:
        if response.status != 200 or urlsplit(response.url).netloc != urlsplit(PUBLIC_URL).netloc:
            raise RuntimeError("Unexpected Pages response/redirect")
        content = response.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise RuntimeError("Public Pages response exceeds safety limit")
        return content


def rebuild(token, *, sleep=time.sleep):
    for attempt in range(3):
        try:
            status, _ = api_request(token, "pages/builds", method="POST")
            if status != 201:
                raise RuntimeError(f"Unexpected Pages build response: {status}")
            print("PAGES_REBUILD_REQUESTED")
            return
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            sleep(20 * (attempt + 1))


def recover(expected, head, *, live, current_head, request_build,
            clock=time.monotonic, sleep=time.sleep,
            warmup=60, settle=480, poll=15):
    """Only a bitwise match is success. Never rebuild if drift isn't observed."""
    digest = hashlib.sha256(expected).hexdigest()

    def observe_for(seconds):
        deadline = clock() + seconds
        observed_mismatch = False
        while True:
            if current_head() != head:
                return "superseded"
            try:
                actual = live(digest)
            except (OSError, URLError, HTTPError, TimeoutError) as exc:
                print(f"PAGES_CHECK_UNAVAILABLE type={type(exc).__name__}")
            else:
                if actual == expected:
                    return "verified" if current_head() == head else "superseded"
                observed_mismatch = True
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep(min(poll, remaining))
        return "stale" if observed_mismatch else "unavailable"

    prior = observe_for(warmup)
    if prior in ("verified", "superseded"):
        return prior
    if prior == "unavailable":
        raise RuntimeError("Pages could not be read: do not force a build without drift evidence")
    if current_head() != head:
        return "superseded"
    request_build()
    after = observe_for(settle)
    if after in ("verified", "superseded"):
        return after
    raise RuntimeError("Pages rebuild did not publish exact index.html bytes")


def main():
    token, sha = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_SHA")
    if not token or not sha:
        raise RuntimeError("Required GitHub runner credentials or source SHA missing")
    expected = Path("index.html").read_bytes()
    if not expected or len(expected) > 10 * 1024 * 1024:
        raise RuntimeError("Source index.html empty or oversized")
    result = recover(
        expected, sha, live=public_page,
        current_head=lambda: source_head(token),
        request_build=lambda: rebuild(token))
    print(f"PAGES_RECOVERY_{result.upper()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, HTTPError) as exc:
        print(f"PAGES_RECOVERY_FAILED type={type(exc).__name__}: {exc}")
        raise SystemExit(1)
