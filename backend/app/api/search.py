from enum import StrEnum

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.network import client_ip
from app.api.videos import _summary_page
from app.catalogue.schemas import ChannelHit, ChannelHitPage, SearchResponse, VideoPageResponse
from app.catalogue.service import VideoCard, VideoPage
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.db.session import get_db
from app.identity.schemas import ErrorResponse
from app.search.service import ChannelHits, VideoHits, search_channels, search_videos

router = APIRouter(tags=["search"])


class SearchType(StrEnum):
    video = "video"
    channel = "channel"
    all = "all"


class SearchSort(StrEnum):
    relevance = "relevance"
    newest = "newest"
    views = "views"


@router.get(
    "/api/v1/search",
    response_model=SearchResponse,
    response_model_exclude_none=True,
    responses={
        400: {"model": ErrorResponse, "description": "The query is blank or the cursor is bad."}
    },
)
def search(
    request: Request,
    q: str = Query(max_length=100),
    result_type: SearchType = Query(default=SearchType.all, alias="type"),
    sort: SearchSort = SearchSort.relevance,
    limit: int = Query(default=24, ge=1, le=50),
    cursor: str | None = None,
    channel_cursor: str | None = None,
    session: Session = Depends(get_db),
) -> SearchResponse:
    """Public catalogue search. Private, unlisted, draft, and hidden videos are not results."""
    enforce_rate_limit(f"rl:search:{client_ip(request)}", 60, 60)
    query = q.strip()
    if not query:
        raise AppError("INVALID_REQUEST", "Enter a search query.", 400)
    videos = None
    channels = None
    if result_type in (SearchType.video, SearchType.all):
        videos = _videos(search_videos(session, query, sort.value, cursor, limit))
    if result_type in (SearchType.channel, SearchType.all):
        channels = _channels(search_channels(session, query, channel_cursor, limit))
    return SearchResponse(videos=videos, channels=channels)


def _videos(page: VideoHits) -> VideoPageResponse:
    cards = tuple(VideoCard(hit.video, hit.channel) for hit in page.items)
    return _summary_page(VideoPage(cards, page.next_cursor))


def _channels(page: ChannelHits) -> ChannelHitPage:
    return ChannelHitPage(
        items=[ChannelHit(handle=item.handle, name=item.name) for item in page.items],
        next_cursor=page.next_cursor,
    )
