"""Moderator and admin actions. Ordinary users cannot set moderation status."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalogue.acl import video_not_found
from app.catalogue.cursors import decode_time_id, encode_time_id
from app.core.exceptions import AppError
from app.core.ids import uuid7
from app.db.models import (
    Channel,
    Comment,
    CommentReport,
    ModerationAction,
    ModerationStatus,
    ReportReason,
    ReportStatus,
    User,
    UserRole,
    Video,
    VideoReport,
)
from app.playback.service import open_playback, revoke_video_playback

_PAGE = 20


def report_video(
    session: Session, reporter: User, video_id: uuid.UUID, reason: ReportReason, details: str | None
) -> VideoReport:
    video = session.get(Video, video_id)
    channel = None if video is None else session.get(Channel, video.channel_id)
    if video is None or channel is None or video.deleted_at is not None:
        raise video_not_found()
    if channel.user_id == reporter.id:
        raise AppError("INVALID_STATE", "You cannot report your own video.", 409)
    from app.catalogue.acl import require_visible_video

    require_visible_video(session, video.id, reporter)
    existing = session.scalar(
        select(VideoReport).where(
            VideoReport.video_id == video.id,
            VideoReport.reporter_user_id == reporter.id,
            VideoReport.status == ReportStatus.open,
        )
    )
    if existing is not None:
        return existing
    row = VideoReport(
        id=uuid7(),
        video_id=video.id,
        reporter_user_id=reporter.id,
        reason=reason,
        details=_details(details),
        status=ReportStatus.open,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()
    return row


def report_comment(
    session: Session,
    reporter: User,
    comment_id: uuid.UUID,
    reason: ReportReason,
    details: str | None,
) -> CommentReport:
    comment = session.get(Comment, comment_id)
    hidden = comment is None or comment.deleted_at is not None
    if hidden or comment.moderation_status is not ModerationStatus.visible:
        raise AppError("COMMENT_NOT_FOUND", "Comment not found.", 404)
    from app.catalogue.acl import require_visible_video

    require_visible_video(session, comment.video_id, reporter)
    if comment.user_id == reporter.id:
        raise AppError("INVALID_STATE", "You cannot report your own comment.", 409)
    existing = session.scalar(
        select(CommentReport).where(
            CommentReport.comment_id == comment.id,
            CommentReport.reporter_user_id == reporter.id,
            CommentReport.status == ReportStatus.open,
        )
    )
    if existing is not None:
        return existing
    row = CommentReport(
        id=uuid7(),
        comment_id=comment.id,
        reporter_user_id=reporter.id,
        reason=reason,
        details=_details(details),
        status=ReportStatus.open,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()
    return row


def list_video_reports(
    session: Session, status: ReportStatus, cursor: str | None, limit: int
) -> dict[str, object]:
    return _report_page(session, VideoReport, status, cursor, limit, "video_id")


def list_comment_reports(
    session: Session, status: ReportStatus, cursor: str | None, limit: int
) -> dict[str, object]:
    return _report_page(session, CommentReport, status, cursor, limit, "comment_id")


def dismiss_report(session: Session, actor: User, report_id: uuid.UUID, reason: str) -> None:
    report = session.get(VideoReport, report_id)
    target_type = "video"
    if report is None:
        comment_report = session.get(CommentReport, report_id)
        if comment_report is None:
            raise AppError("REPORT_NOT_FOUND", "Report not found.", 404)
        report = comment_report
        target_type = "comment"
    if report.status is not ReportStatus.open:
        session.commit()
        return
    now = datetime.now(UTC)
    report.status = ReportStatus.dismissed
    report.resolved_at = now
    _audit(
        session, actor, "dismiss_report", "report", report.id, reason, {"target_type": target_type}
    )
    session.commit()


def hide_video(
    session: Session, actor: User, video_id: uuid.UUID, reason: str, report_id: uuid.UUID | None
) -> None:
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise video_not_found()
    now = datetime.now(UTC)
    if video.moderation_status is not ModerationStatus.hidden:
        video.moderation_status = ModerationStatus.hidden
        video.acl_version += 1
        _audit(session, actor, "hide_video", "video", video.id, reason, {})
    _close_report(session, report_id, now, video.id)
    revoke_video_playback(session, video.id, now)
    session.commit()


def restore_video(session: Session, actor: User, video_id: uuid.UUID, reason: str) -> None:
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None:
        raise video_not_found()
    if video.moderation_status is not ModerationStatus.visible:
        video.moderation_status = ModerationStatus.visible
        video.acl_version += 1
        _audit(session, actor, "restore_video", "video", video.id, reason, {})
    session.commit()


def hide_comment(session: Session, actor: User, comment_id: uuid.UUID, reason: str) -> None:
    comment = session.get(Comment, comment_id)
    if comment is None:
        raise AppError("COMMENT_NOT_FOUND", "Comment not found.", 404)
    video = session.scalar(select(Video).where(Video.id == comment.video_id).with_for_update())
    if (
        comment.deleted_at is None
        and comment.moderation_status is ModerationStatus.visible
        and video is not None
    ):
        video.comment_count = max(0, video.comment_count - 1)
    if comment.moderation_status is not ModerationStatus.hidden:
        comment.moderation_status = ModerationStatus.hidden
        _audit(session, actor, "hide_comment", "comment", comment.id, reason, {})
    session.commit()


def restore_comment(session: Session, actor: User, comment_id: uuid.UUID, reason: str) -> None:
    comment = session.get(Comment, comment_id)
    if comment is None:
        raise AppError("COMMENT_NOT_FOUND", "Comment not found.", 404)
    video = session.scalar(select(Video).where(Video.id == comment.video_id).with_for_update())
    if comment.moderation_status is not ModerationStatus.visible:
        comment.moderation_status = ModerationStatus.visible
        if comment.deleted_at is None and video is not None:
            video.comment_count += 1
        _audit(session, actor, "restore_comment", "comment", comment.id, reason, {})
    session.commit()


def video_history(
    session: Session, video_id: uuid.UUID, cursor: str | None, limit: int
) -> dict[str, object]:
    video = session.get(Video, video_id)
    if video is None:
        raise video_not_found()
    filters = [ModerationAction.target_type == "video", ModerationAction.target_id == video.id]
    if cursor is not None:
        moment, action_id = decode_time_id(cursor, "history")
        filters.append(ModerationAction.created_at < moment)
        filters.append(ModerationAction.id != action_id)
    rows = list(
        session.scalars(
            select(ModerationAction)
            .where(*filters)
            .order_by(ModerationAction.created_at.desc(), ModerationAction.id.desc())
            .limit(limit + 1)
        ).all()
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_time_id("history", last.created_at, last.id)
    return {"items": [_action(row) for row in page], "next_cursor": next_cursor}


def moderator_playback(
    session: Session, actor: User, video_id: uuid.UUID, cookie_value: str | None
):
    video = session.get(Video, video_id)
    if video is None or video.deleted_at is not None or video.processing_status.value != "ready":
        raise video_not_found()
    reported = session.scalar(
        select(VideoReport.id).where(VideoReport.video_id == video.id).limit(1)
    )
    private_unreported = video.visibility.value == "private" and reported is None
    if video.moderation_status is not ModerationStatus.hidden and private_unreported:
        raise video_not_found()
    opened = open_playback(
        session,
        video.id,
        actor,
        cookie_value,
        moderation_access=True,
        commit=False,
    )
    if video.moderation_status is ModerationStatus.hidden or video.visibility.value == "private":
        _audit(session, actor, "view_private_video", "video", video.id, "moderation playback", {})
    session.commit()
    return opened


def change_role(
    session: Session, actor: User, user_id: uuid.UUID, role: UserRole, reason: str
) -> None:
    target = session.get(User, user_id)
    if target is None:
        raise AppError("USER_NOT_FOUND", "User not found.", 404)
    if target.role is UserRole.ADMIN and role is not UserRole.ADMIN:
        _require_another_admin(session, target.id)
    target.role = role
    target.updated_at = datetime.now(UTC)
    _audit(session, actor, "change_role", "user", target.id, reason, {"role": role.value})
    session.commit()


def set_activation(
    session: Session, actor: User, user_id: uuid.UUID, active: bool, reason: str
) -> None:
    target = session.get(User, user_id)
    if target is None:
        raise AppError("USER_NOT_FOUND", "User not found.", 404)
    now = datetime.now(UTC)
    if active:
        if not target.is_active:
            target.is_active = True
            target.updated_at = now
            _audit(session, actor, "reactivate_user", "user", target.id, reason, {})
        session.commit()
        return
    if not target.is_active:
        session.commit()
        return
    if target.role is UserRole.ADMIN:
        _require_another_admin(session, target.id)
    from app.identity.service import deactivate_user

    _audit(session, actor, "deactivate_user", "user", target.id, reason, {})
    deactivate_user(session, target)


def _require_another_admin(session: Session, user_id: uuid.UUID) -> None:
    others = session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.ADMIN, User.is_active.is_(True), User.id != user_id)
    )
    if int(others or 0) < 1:
        raise AppError("INVALID_STATE", "The last active admin cannot be removed.", 409)


def _close_report(
    session: Session, report_id: uuid.UUID | None, now: datetime, video_id: uuid.UUID
) -> None:
    if report_id is None:
        return
    report = session.get(VideoReport, report_id)
    if report is None or report.video_id != video_id or report.status is not ReportStatus.open:
        return
    report.status = ReportStatus.action_taken
    report.resolved_at = now


def _audit(
    session: Session,
    actor: User,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    reason: str,
    metadata: dict,
) -> None:
    session.add(
        ModerationAction(
            id=uuid7(),
            actor_user_id=actor.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason=reason.strip() or "unspecified",
            metadata_json=metadata,
            created_at=datetime.now(UTC),
        )
    )


def _action(row: ModerationAction) -> dict[str, object]:
    return {
        "id": row.id,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "reason": row.reason,
        "created_at": row.created_at,
    }


def _report_page(
    session: Session, model, status: ReportStatus, cursor: str | None, limit: int, id_field: str
):
    filters = [model.status == status]
    if cursor is not None:
        moment, report_id = decode_time_id(cursor, "reports")
        filters.append(model.created_at < moment)
        filters.append(model.id != report_id)
    rows = list(
        session.scalars(
            select(model)
            .where(*filters)
            .order_by(model.created_at.desc(), model.id.desc())
            .limit(limit + 1)
        ).all()
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_time_id("reports", last.created_at, last.id)
    items = []
    for row in page:
        items.append(
            {
                "id": row.id,
                id_field: getattr(row, id_field),
                "reason": row.reason,
                "details": row.details,
                "status": row.status,
                "created_at": row.created_at,
            }
        )
    return {"items": items, "next_cursor": next_cursor}


def _details(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
