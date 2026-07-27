#!/usr/bin/env python3
"""Probe every Spotify Web API endpoint this app depends on.

Development Mode endpoint availability is the biggest unknown in the design, so run
this once against a real account before trusting any of it:

    pip install -r requirements.txt
    python scripts/probe_api.py

Reads CLIENT_ID / CLIENT_SECRET / REDIRECT_URI from .env, opens a browser for consent,
then reports the real status code for each call. Creates and deletes one throwaway
playlist named "favsongs probe (safe to delete)".

Pass a Discover Weekly share link to also confirm that reading it is blocked:

    python scripts/probe_api.py https://open.spotify.com/playlist/<id>
"""

import os
import re
import sys
from importlib.metadata import version

import requests
from spotipy.oauth2 import SpotifyOAuth

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import SCOPES  # noqa: E402

API = "https://api.spotify.com/v1"
PROBE_PLAYLIST_NAME = "favsongs probe (safe to delete)"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

results: list[tuple[bool, str]] = []


def check(label: str, response: requests.Response, expected: set[int], note: str = "") -> bool:
    ok = response.status_code in expected
    colour = GREEN if ok else RED
    mark = "PASS" if ok else "FAIL"
    want = "" if ok else f"  {DIM}(wanted {sorted(expected)}){RESET}"
    print(f"  {colour}{mark}{RESET}  {response.status_code}  {label}{want}")
    if note:
        print(f"        {DIM}{note}{RESET}")
    if not ok:
        body = response.text[:300].replace("\n", " ")
        print(f"        {DIM}{body}{RESET}")
    results.append((ok, label))
    return ok


def playlist_id_from(value: str) -> str:
    match = re.search(r"(?:playlist[/:])([A-Za-z0-9]+)", value)
    return match.group(1) if match else value


def main() -> int:
    auth = SpotifyOAuth(
        client_id=os.environ["CLIENT_ID"],
        client_secret=os.environ["CLIENT_SECRET"],
        redirect_uri=os.environ["REDIRECT_URI"],
        scope=" ".join(SCOPES),
        cache_path=os.path.join(os.path.dirname(__file__), ".probe-token-cache"),
        open_browser=True,
    )
    token = auth.get_access_token(as_dict=False)
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    print(f"\n{DIM}spotipy {version('spotipy')} against {API}{RESET}\n")

    print("Identity")
    me = session.get(f"{API}/me", timeout=20)
    check("GET /me", me, {200})
    if me.status_code != 200:
        print(f"\n{RED}Cannot continue without /me.{RESET}")
        return 1
    profile = me.json()
    print(f"        {DIM}{profile.get('display_name')} · product={profile.get('product')}{RESET}")
    if profile.get("product") != "premium":
        print(
            f"  {YELLOW}WARN{RESET}  Development Mode requires the app owner to have Premium."
        )

    print("\nPlay tracking")
    recent = session.get(f"{API}/me/player/recently-played", params={"limit": 50}, timeout=20)
    check("GET /me/player/recently-played", recent, {200})

    seed_track = None
    context_uris: set[str] = set()
    if recent.status_code == 200:
        items = recent.json().get("items", [])
        with_context = sum(1 for item in items if item.get("context"))
        for item in items:
            context = item.get("context") or {}
            if context.get("uri"):
                context_uris.add(context["uri"])
            track = item.get("track") or {}
            if not seed_track and track.get("id"):
                seed_track = track["id"]
        print(
            f"        {DIM}{len(items)} items, {with_context} carry a context "
            f"({len(context_uris)} distinct){RESET}"
        )
        if items and with_context == 0:
            print(
                f"  {YELLOW}WARN{RESET}  No contexts in recently-played. Discovery capture "
                f"relies on /me/player instead, which is checked below."
            )

    player = session.get(f"{API}/me/player", timeout=20)
    check(
        "GET /me/player",
        player,
        {200, 204},
        "204 just means nothing is playing right now."
        if player.status_code == 204
        else "",
    )
    if player.status_code == 200:
        context = (player.json() or {}).get("context") or {}
        if context.get("uri"):
            print(f"        {DIM}context.uri = {context['uri']}{RESET}")
        else:
            print(
                f"  {YELLOW}WARN{RESET}  Playing, but no context.uri. Play from a playlist "
                f"to confirm discovery capture can see it."
            )

    print("\nPlaylists")
    mine = session.get(f"{API}/me/playlists", params={"limit": 50}, timeout=20)
    check("GET /me/playlists", mine, {200})
    if mine.status_code == 200:
        items = mine.json().get("items", [])
        private = sum(1 for p in items if p and p.get("public") is False)
        print(
            f"        {DIM}{len(items)} playlists, {private} private "
            f"(private > 0 confirms playlist-read-private){RESET}"
        )

    created = session.post(
        f"{API}/me/playlists",
        json={
            "name": PROBE_PLAYLIST_NAME,
            "public": False,
            "description": "Temporary. Created by scripts/probe_api.py.",
        },
        timeout=20,
    )
    check("POST /me/playlists", created, {200, 201})

    probe_id = created.json().get("id") if created.status_code in {200, 201} else None
    if probe_id and seed_track:
        added = session.post(
            f"{API}/playlists/{probe_id}/items",
            json={"uris": [f"spotify:track:{seed_track}"]},
            timeout=20,
        )
        check("POST /playlists/{id}/items", added, {200, 201})

        listed = session.get(
            f"{API}/playlists/{probe_id}/items", params={"limit": 50}, timeout=20
        )
        check("GET /playlists/{id}/items", listed, {200})

        removed = session.delete(
            f"{API}/playlists/{probe_id}/items",
            json={"items": [{"uri": f"spotify:track:{seed_track}"}]},
            timeout=20,
        )
        check("DELETE /playlists/{id}/items", removed, {200})
    elif probe_id:
        print(f"  {YELLOW}SKIP{RESET}  No seed track from recently-played; item calls skipped.")

    if probe_id:
        session.delete(f"{API}/playlists/{probe_id}/followers", timeout=20)
        print(f"        {DIM}cleaned up probe playlist {probe_id}{RESET}")

    print("\nDiscover Weekly (expected to be blocked)")
    targets = [playlist_id_from(a) for a in sys.argv[1:]]
    targets += [uri.split(":")[-1] for uri in context_uris if uri.startswith("spotify:playlist:")]
    if not targets:
        print(
            f"  {YELLOW}SKIP{RESET}  Pass a Discover Weekly link as an argument to check this."
        )
    for target in dict.fromkeys(targets):
        blocked = session.get(f"{API}/playlists/{target}/items", params={"limit": 1}, timeout=20)
        if blocked.status_code == 200:
            owner = "?"
            meta = session.get(f"{API}/playlists/{target}", timeout=20)
            if meta.status_code == 200:
                owner = (meta.json().get("owner") or {}).get("id", "?")
            print(
                f"  {DIM}note{RESET}  200  playlist {target} is readable (owner={owner}) "
                f"-- a playlist you own, not an algorithmic one."
            )
        else:
            print(
                f"  {GREEN}PASS{RESET}  {blocked.status_code}  playlist {target} unreadable "
                f"-- confirms the context-capture workaround is required."
            )

    failed = [label for ok, label in results if not ok]
    print()
    if failed:
        print(f"{RED}{len(failed)} check(s) failed:{RESET}")
        for label in failed:
            print(f"  - {label}")
        return 1
    print(f"{GREEN}All {len(results)} checks passed.{RESET}")
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    raise SystemExit(main())
