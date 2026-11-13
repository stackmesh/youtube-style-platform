"""Pure counters for reactions and playlist positions."""


def reaction_deltas(previous: str | None, new: str | None) -> tuple[int, int]:
    """Return like and dislike deltas for one user's reaction change."""
    likes = 0
    dislikes = 0
    if previous == "like":
        likes -= 1
    elif previous == "dislike":
        dislikes -= 1
    if new == "like":
        likes += 1
    elif new == "dislike":
        dislikes += 1
    return likes, dislikes


def end_position(positions: list[int]) -> int:
    """Next playlist position, spaced by 1000."""
    if not positions:
        return 1000
    return max(positions) + 1000
