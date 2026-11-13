from app.social.rules import end_position, reaction_deltas


def test_reaction_switch_adjusts_both_counters() -> None:
    assert reaction_deltas(None, "like") == (1, 0)
    assert reaction_deltas("like", "like") == (0, 0)
    assert reaction_deltas("like", "dislike") == (-1, 1)
    assert reaction_deltas("dislike", None) == (0, -1)


def test_playlist_positions_are_spaced() -> None:
    assert end_position([]) == 1000
    assert end_position([1000, 3000]) == 4000
