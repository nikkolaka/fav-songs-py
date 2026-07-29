"""How much of a track was heard.

This is the number the whole feature turns on: it decides what reaches a playlist. The
cases that matter are the ones where playback position and audio heard disagree --
seeking, pausing, and the gap between the last poll and the end of the track.
"""

from pytest import approx

from app.listens import (
    Observation,
    continues,
    finalize,
    observation_from_playback,
    observe,
    start_session,
)

MINUTE = 60_000
TRACK_MS = 180_000  # three minutes


def obs(progress_ms, at, is_playing=True, track_id="t1", duration_ms=TRACK_MS):
    return Observation(
        track_id=track_id,
        name="Song",
        artist="Artist",
        duration_ms=duration_ms,
        progress_ms=progress_ms,
        is_playing=is_playing,
        context_uri=None,
        at=at,
    )


def played(steps, ended_at, duration_ms=TRACK_MS):
    """Run a session through a list of (progress_ms, at) polls and close it."""
    session = start_session(obs(*steps[0], duration_ms=duration_ms))
    for progress_ms, at in steps[1:]:
        observe(session, obs(progress_ms, at, duration_ms=duration_ms))
    return finalize(session, ended_at)


# ------------------------------------------------------------------ the tail


def test_a_full_listen_reaches_one_even_though_no_poll_saw_the_end():
    """The last poll of a 3-minute track sits ~2:50 in. Without crediting the unobserved
    tail, every complete listen would cap at ~94% and a 95% threshold would be
    unreachable."""
    steps = [(i * 30_000, 1_000_000 + i * 30_000) for i in range(6)]  # 0:00 .. 2:30
    result = played(steps, ended_at=1_000_000 + TRACK_MS)

    assert result["completion_ratio"] == 1.0
    assert result["listened_ms"] == TRACK_MS


def test_the_tail_is_capped_by_what_was_left_of_the_track():
    """Ten minutes of silence after a track ends is not ten minutes of listening."""
    result = played([(0, 1_000_000), (150_000, 1_150_000)], ended_at=1_150_000 + 10 * MINUTE)
    assert result["listened_ms"] == TRACK_MS


def test_a_paused_track_gets_no_tail():
    session = start_session(obs(0, 1_000_000))
    observe(session, obs(60_000, 1_060_000))
    observe(session, obs(60_000, 1_090_000, is_playing=False))

    result = finalize(session, 1_090_000 + 5 * MINUTE)
    assert result["listened_ms"] == 60_000
    assert result["completion_ratio"] == approx(60_000 / TRACK_MS)


# --------------------------------------------------------------- not hearing


def test_skipping_early_leaves_it_far_short():
    result = played([(0, 1_000_000)], ended_at=1_020_000)
    assert result["listened_ms"] == 20_000
    assert result["completion_ratio"] < 0.2


def test_seeking_forward_credits_time_not_position():
    """Dragging to the last 10 seconds is not listening to the track, even though
    `progress_ms` says 97%."""
    session = start_session(obs(0, 1_000_000))
    observe(session, obs(170_000, 1_030_000))  # 30s later, 170s further in

    result = finalize(session, 1_040_000)
    assert result["listened_ms"] < 55_000
    assert result["completion_ratio"] < 0.3


def test_a_track_joined_late_counts_from_its_start():
    """Polling picks a track up 40 seconds in because the interval backed off, not
    because those 40 seconds went unheard."""
    result = played([(40_000, 1_000_000), (70_000, 1_030_000)], ended_at=1_030_000 + 110_000)
    assert result["completion_ratio"] == 1.0


def test_a_zero_duration_track_never_qualifies():
    result = played([(0, 1_000_000)], ended_at=1_100_000, duration_ms=0)
    assert result["completion_ratio"] == 0.0


# ------------------------------------------------------------ session bounds


def test_a_different_track_is_a_different_session():
    session = start_session(obs(0, 1_000_000))
    assert not continues(session, obs(0, 1_030_000, track_id="t2"))


def test_seeking_backwards_stays_in_the_same_session():
    session = start_session(obs(0, 1_000_000))
    observe(session, obs(120_000, 1_120_000))
    assert continues(session, obs(100_000, 1_150_000))


def test_restarting_from_the_top_is_a_new_session():
    session = start_session(obs(0, 1_000_000))
    observe(session, obs(120_000, 1_120_000))
    assert not continues(session, obs(1_000, 1_150_000))


def test_the_same_track_after_a_long_gap_is_a_new_session():
    session = start_session(obs(0, 1_000_000))
    assert not continues(session, obs(30_000, 1_000_000 + TRACK_MS + 5 * MINUTE))


# ------------------------------------------------------------- reading input


def test_episodes_are_not_listens():
    assert (
        observation_from_playback(
            {"item": {"id": "e1", "type": "episode", "duration_ms": 1}, "progress_ms": 0}, 1
        )
        is None
    )


def test_nothing_playing_is_no_observation():
    assert observation_from_playback(None, 1) is None
    assert observation_from_playback({"item": None}, 1) is None
