"""Demo data for local development without a Spotify login."""

import time

_MS = int(time.time() * 1000)

def demo_state() -> dict:
    return {
        "connected": True,
        "user": {
            "display_name": "Demo User",
            "spotify_user_id": "demo",
        },
        "tracker_running": True,
        "last_error": None,
        "now_playing": {
            "track_id": "t2",
            "name": "Motion Sickness",
            "artist": "Phoebe Bridgers",
            "duration_ms": 228000,
            "progress_ms": 134000,
            "completion_ratio": 0.59,
            "heard_ms": 134000,
            "heard_ratio": 0.79,
            "counts": True,
            "is_playing": True,
        },
        "settings": {
            "favorite_threshold": 3,
            "min_completion_ratio": 0.5,
            "playlist_name": "Favourite Songs",
            "playlist_public": False,
            "auto_add_enabled": True,
            "discovery_enabled": True,
            "favorites_playlist_id": "demo-playlist",
            "pinned_stats": [],
        },
        "stats": {
            "last_24h": {"total": 34, "qualified": 27},
            "tracked_tracks": 142,
            "next_favorite": {
                "track_id": "t6",
                "name": "Simulation Swarm",
                "artist": "Big Thief",
                "qualified_plays": 2,
            },
        },
        "favorites": [
            {
                "track_id": "t1",
                "name": "Not Strong Enough",
                "artist": "boygenius",
                "qualified_plays": 8,
                "total_plays": 11,
                "last_played": _MS - 300_000,
                "in_playlist": True,
            },
            {
                "track_id": "t2",
                "name": "Motion Sickness",
                "artist": "Phoebe Bridgers",
                "qualified_plays": 7,
                "total_plays": 9,
                "last_played": _MS - 600_000,
                "in_playlist": True,
            },
            {
                "track_id": "t3",
                "name": "Cayendo",
                "artist": "Frank Ocean",
                "qualified_plays": 5,
                "total_plays": 7,
                "last_played": _MS - 1_200_000,
                "in_playlist": True,
            },
            {
                "track_id": "t4",
                "name": "A&W",
                "artist": "Lana Del Rey",
                "qualified_plays": 4,
                "total_plays": 6,
                "last_played": _MS - 3_600_000,
                "in_playlist": True,
            },
            {
                "track_id": "t5",
                "name": "Welcome to Hell",
                "artist": "black midi",
                "qualified_plays": 3,
                "total_plays": 5,
                "last_played": _MS - 7_200_000,
                "in_playlist": True,
            },
            {
                "track_id": "t6",
                "name": "Simulation Swarm",
                "artist": "Big Thief",
                "qualified_plays": 2,
                "total_plays": 4,
                "last_played": _MS - 14_400_000,
                "in_playlist": False,
            },
        ],
        "favorite_track_ids": ["t1", "t2"],
        "discovery": {
            "month": "2026-07",
            "month_name": "July's Discover",
            "tracks": [
                {
                    "track_id": "d1",
                    "name": "The Bug Collector",
                    "artist": "Haley Heynderickx",
                    "added_at": int(time.time()) - 86400,
                },
                {
                    "track_id": "d2",
                    "name": "Certainty",
                    "artist": "Big Thief",
                    "added_at": int(time.time()) - 86400 * 2,
                },
                {
                    "track_id": "d3",
                    "name": "Thumbs Again",
                    "artist": "Lucy Dacus",
                    "added_at": int(time.time()) - 86400 * 3,
                },
                {
                    "track_id": "d4",
                    "name": "Good Luck, Babe!",
                    "artist": "Chappell Roan",
                    "added_at": int(time.time()) - 86400 * 5,
                },
            ],
            "blocked": [
                {
                    "track_id": "b1",
                    "name": "Neon Dreams",
                    "artist": "AImusic Official",
                    "reason": "AI-generated artist",
                    "blocked_at": "2026-07-28",
                },
            ],
            "months": [
                {"month": "2026-07", "name": "July's Discover", "tracks": 4},
                {"month": "2026-06", "name": "June's Discover", "tracks": 12},
                {"month": "2026-05", "name": "May's Discover", "tracks": 8},
            ],
            "sources": [
                {"playlist_id": "37i9dQZEVXcJZyENOWUFo7", "label": "Discover Weekly", "degraded": None},
                {"playlist_id": "37i9dQZEVXcNxFI8Cj0jJA", "label": "Release Radar", "degraded": None},
            ],
            "unlabelled": [],
            "blocklist": {
                "artists": 7441,
                "fetched_at": int(time.time()) - 3600,
                "error": None,
            },
        },
    }


def demo_history(query: str = "", start: int = 0, end: int = 0, qualified: bool = None,
                 cursor: str = None, limit: int = 50) -> dict:
    _now = _MS
    _day = 86_400_000

    all_items = [
        # Today
        ("Out of Time", "The Weeknd", _now - 600_000, 1.0, True, False),
        ("Motion Sickness", "Phoebe Bridgers", _now - 1_800_000, 0.59, True, True),
        ("Cayendo", "Frank Ocean", _now - 3_600_000, 0.94, True, False),
        ("Too Much", "girl in red", _now - 7_200_000, 0.12, False, False),
        ("Chaeri", "Magdalena Bay", _now - 14_400_000, 0.98, True, False),
        ("Simulation Swarm", "Big Thief", _now - 21_600_000, 0.67, True, False),
        ("Welcome to Hell", "black midi", _now - 28_800_000, 0.87, True, False),
        ("Linger", "The Cranberries", _now - 32_400_000, 0.22, False, False),
        # Yesterday
        ("Not Strong Enough", "boygenius", _now - _day * 1 - 3_600_000, 1.0, True, False),
        ("A&W", "Lana Del Rey", _now - _day * 1 - 7_200_000, 0.75, True, False),
        ("Steeeam", "Shelly", _now - _day * 1 - 10_800_000, 0.08, False, False),
        ("Bloodbuzz Ohio", "The National", _now - _day * 1 - 14_400_000, 0.82, True, False),
        ("Sun Bleached Flies", "Ethel Cain", _now - _day * 1 - 18_000_000, 0.91, True, False),
        ("Chaeri", "Magdalena Bay", _now - _day * 1 - 21_600_000, 0.55, True, False),
        # 2 days ago
        ("Welcome to Hell", "black midi", _now - _day * 2 - 3_600_000, 0.93, True, False),
        ("The Bug Collector", "Haley Heynderickx", _now - _day * 2 - 10_800_000, 0.78, True, False),
        ("Certainty", "Big Thief", _now - _day * 2 - 18_000_000, 0.44, False, False),
        ("Posing in Bondage", "Japanese Breakfast", _now - _day * 2 - 25_200_000, 0.95, True, False),
        # 3 days ago
        ("Good Luck, Babe!", "Chappell Roan", _now - _day * 3 - 7_200_000, 1.0, True, False),
        ("Motion Sickness", "Phoebe Bridgers", _now - _day * 3 - 14_400_000, 0.81, True, False),
        ("Not Strong Enough", "boygenius", _now - _day * 3 - 21_600_000, 0.35, False, False),
        ("Thumbs Again", "Lucy Dacus", _now - _day * 3 - 28_800_000, 0.88, True, False),
        # 7 days ago
        ("A&W", "Lana Del Rey", _now - _day * 7 - 3_600_000, 0.96, True, False),
        ("Too Much", "girl in red", _now - _day * 7 - 10_800_000, 0.19, False, False),
        ("Cayendo", "Frank Ocean", _now - _day * 7 - 18_000_000, 1.0, True, False),
    ]

    filtered = list(all_items)
    if query:
        q = query.lower()
        filtered = [i for i in filtered if q in i[0].lower() or q in i[1].lower()]
    if qualified is not None:
        filtered = [i for i in filtered if i[4] == qualified]
    if start:
        filtered = [i for i in filtered if i[2] >= start]
    if end:
        filtered = [i for i in filtered if i[2] <= end]

    if cursor:
        parts = cursor.split("|")
        if len(parts) == 3:
            try:
                cur_val, cur_id = float(parts[1]), int(parts[2])
                filtered = [i for i in filtered if i[2] < cur_val or (i[2] == cur_val and i <= cur_id)]
            except (ValueError, IndexError):
                pass

    page = filtered[:limit]
    next_cursor = None
    if len(filtered) > limit:
        last = page[-1]
        idx = page.index(last)
        next_cursor = f"time|{last[2]}|{idx}"

    return {
        "items": [
            {
                "track_id": f"h{i}",
                "name": name,
                "artist": artist,
                "played_at": ts,
                "completion_ratio": ratio,
                "qualified": qual,
                "is_open": open_,
                "play_count": (i % 15) + 1,
            }
            for i, (name, artist, ts, ratio, qual, open_) in enumerate(page)
        ],
        "next_cursor": next_cursor,
    }


def demo_history_summary() -> dict:
    return {
        "listens": 471,
        "qualified": 382,
        "tracks": 94,
        "first_played": "2026-05-12",
    }
