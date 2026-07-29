"""Measuring how much of a track was actually heard.

`GET /me/player/recently-played` says *that* a track was played and never how far
through it you got, so completion cannot come from there. It has to be measured from
`GET /me/player`, which does report `progress_ms` -- by watching one track across
consecutive polls and accumulating the audio that went past.

A *session* is one continuous playback of one track. It opens when a track first
appears, absorbs every poll that still looks like the same playback, and closes when
the track changes or playback goes away. On close it yields a completion ratio, and
that ratio is what decides whether the listen counts.

Two things this deliberately does not do:

- **Trust `progress_ms` deltas alone.** Seeking forward moves progress without any
  audio being heard, so each poll credits at most the wall-clock time that actually
  elapsed. Skipping through a track leaves it far short of the threshold, as it should.
- **Require a poll to land on the final second.** With a 30s interval the last poll of a
  three-minute track sits ~2:50 in, which would cap every complete listen at ~94%. When
  a session closes we know when the *next* track started (`now - its progress`), so the
  unobserved tail is credited up to the track's remaining duration and a full play
  reaches 1.0.
"""

from dataclasses import dataclass
from typing import Any, Optional

# Clock skew between Spotify's progress_ms and our wall clock. Small, but without it a
# poll that arrives a hair early credits nothing for that interval.
SEEK_TOLERANCE_MS = 2_000

# Progress back below this after having been well past it reads as a replay from the
# top, not a seek backwards -- the one case where the same track means a new session.
RESTART_PROGRESS_MS = 10_000
RESTART_MIN_HEARD_MS = 25_000

# If a track reappears after a gap longer than its own duration it cannot be the same
# playback still running; too much happened in between to account for.
STALE_GAP_MS = 60_000


@dataclass
class Observation:
    """One poll of `GET /me/player`, reduced to what session accounting needs."""

    track_id: str
    name: str
    artist: str
    duration_ms: int
    progress_ms: int
    is_playing: bool
    context_uri: Optional[str]
    at: int  # wall clock, epoch ms


@dataclass
class Session:
    """One continuous playback of one track, still open."""

    track_id: str
    name: str
    artist: str
    context_uri: Optional[str]
    duration_ms: int
    started_at: int  # epoch ms, when this playback began
    listened_ms: int  # audio credited so far
    max_progress_ms: int
    last_progress_ms: int
    last_observed_at: int
    was_playing: bool
    row_id: Optional[int] = None

    @property
    def heard_ratio(self) -> float:
        if self.duration_ms <= 0:
            return 0.0
        return min(1.0, self.listened_ms / self.duration_ms)


def observation_from_playback(
    playback: Optional[dict[str, Any]], at: int
) -> Optional[Observation]:
    """Reduce a playback payload to an Observation, or None if there's nothing to count.

    Episodes are dropped: they have ids and durations like tracks, but a podcast has no
    business in a favourites playlist.
    """
    if not playback:
        return None
    item = playback.get("item") or {}
    track_id = item.get("id")
    if not track_id:
        return None
    if item.get("type") and item.get("type") != "track":
        return None

    artists = item.get("artists") or []
    return Observation(
        track_id=str(track_id),
        name=str(item.get("name") or "Unknown Track"),
        artist=str(
            (artists[0].get("name") if artists and artists[0] else None) or "Unknown Artist"
        ),
        duration_ms=max(int(item.get("duration_ms") or 0), 0),
        progress_ms=max(int(playback.get("progress_ms") or 0), 0),
        is_playing=bool(playback.get("is_playing")),
        context_uri=((playback.get("context") or {}).get("uri")),
        at=at,
    )


def start_session(obs: Observation) -> Session:
    """Open a session, crediting the audio that ran before we first saw it.

    A track already 40 seconds in when we poll was playing for those 40 seconds, so they
    are credited. The exception -- someone seeking forward before our first sighting --
    is rare enough to be worth the accuracy everywhere else.
    """
    return Session(
        track_id=obs.track_id,
        name=obs.name,
        artist=obs.artist,
        context_uri=obs.context_uri,
        duration_ms=obs.duration_ms,
        started_at=obs.at - obs.progress_ms,
        listened_ms=min(obs.progress_ms, obs.duration_ms or obs.progress_ms),
        max_progress_ms=obs.progress_ms,
        last_progress_ms=obs.progress_ms,
        last_observed_at=obs.at,
        was_playing=obs.is_playing,
    )


def continues(session: Session, obs: Observation) -> bool:
    """Is this observation the same playback the session is already tracking?"""
    if obs.track_id != session.track_id:
        return False
    if obs.at - session.last_observed_at > max(session.duration_ms, 0) + STALE_GAP_MS:
        return False
    replayed = (
        obs.progress_ms <= RESTART_PROGRESS_MS
        and session.max_progress_ms >= RESTART_PROGRESS_MS + RESTART_MIN_HEARD_MS
    )
    return not replayed


def observe(session: Session, obs: Observation) -> None:
    """Fold one poll into an open session."""
    elapsed = max(0, obs.at - session.last_observed_at)
    advanced = obs.progress_ms - session.last_progress_ms
    if advanced > 0:
        # Credit the audio that went past, never more than the time that did.
        session.listened_ms += min(advanced, elapsed + SEEK_TOLERANCE_MS)

    session.max_progress_ms = max(session.max_progress_ms, obs.progress_ms)
    session.last_progress_ms = obs.progress_ms
    session.last_observed_at = obs.at
    session.was_playing = obs.is_playing
    session.name = obs.name
    session.artist = obs.artist
    if obs.duration_ms:
        session.duration_ms = obs.duration_ms
    if obs.context_uri:
        session.context_uri = obs.context_uri


def finalize(session: Session, ended_at: int) -> dict[str, Any]:
    """Close a session and report what was heard.

    `ended_at` is when playback of this track stopped. When another track is already
    playing that is known precisely (its start time); when playback simply vanished it
    is the time we noticed.
    """
    tail = 0
    if session.was_playing:
        # Between the last poll and the end, playback kept running -- but only up to
        # what was left of the track.
        window = max(0, ended_at - session.last_observed_at)
        tail = min(window, max(0, session.duration_ms - session.max_progress_ms))

    listened_ms = session.listened_ms + tail
    if session.duration_ms > 0:
        listened_ms = min(listened_ms, session.duration_ms)
        ratio = listened_ms / session.duration_ms
    else:
        ratio = 0.0

    return {
        "track_id": session.track_id,
        "name": session.name,
        "artist": session.artist,
        "context_uri": session.context_uri,
        "played_at": session.started_at,
        "duration_ms": session.duration_ms,
        "listened_ms": listened_ms,
        "completion_ratio": ratio,
    }
