"""Persistence helpers for opaque refresh-token sessions."""

import time
import uuid
from typing import Optional

from open_webui.internal.db import get_db
from open_webui.models.refresh_sessions import RefreshSession, RefreshSessionModel


class RefreshSessionsTable:
    """CRUD helpers for refresh sessions backed by the database table."""

    @staticmethod
    def _resolve_session_id(session_id: Optional[str]) -> str:
        if session_id is None:
            return str(uuid.uuid4())

        try:
            uuid.UUID(session_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("session_id must be a valid UUID string") from exc

        return session_id

    def create_session(
        self,
        user_id: str,
        token_hash: str,
        expires_at: int,
        session_id: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> Optional[RefreshSessionModel]:
        """Create a refresh-session row.

        A caller may provide ``session_id`` because the opaque refresh token embeds
        that identifier before the row is persisted. Initial sign-in creates the
        token first, then stores the matching row under the same id.
        """
        current_time = int(time.time())
        resolved_session_id = self._resolve_session_id(session_id)

        with get_db() as db:
            refresh_session = RefreshSessionModel(
                id=resolved_session_id,
                user_id=user_id,
                token_hash=token_hash,
                created_at=current_time,
                updated_at=current_time,
                expires_at=expires_at,
                revoked_at=None,
                meta=meta,
            )

            new_refresh_session = RefreshSession(**refresh_session.model_dump())
            db.add(new_refresh_session)
            db.commit()
            db.refresh(new_refresh_session)

            return RefreshSessionModel.model_validate(new_refresh_session)

    def get_session_by_id(self, session_id: str) -> Optional[RefreshSessionModel]:
        with get_db() as db:
            refresh_session = (
                db.query(RefreshSession).filter(RefreshSession.id == session_id).first()
            )
            return (
                RefreshSessionModel.model_validate(refresh_session)
                if refresh_session
                else None
            )

    def get_active_session_by_id(
        self, session_id: str, current_time: Optional[int] = None
    ) -> Optional[RefreshSessionModel]:
        active_time = current_time if current_time is not None else int(time.time())

        with get_db() as db:
            refresh_session = (
                db.query(RefreshSession)
                .filter(RefreshSession.id == session_id)
                .filter(RefreshSession.revoked_at.is_(None))
                .filter(RefreshSession.expires_at > active_time)
                .first()
            )
            return (
                RefreshSessionModel.model_validate(refresh_session)
                if refresh_session
                else None
            )

    def rotate_session_token(
        self,
        session_id: str,
        token_hash: str,
        expires_at: int,
        current_token_hash: Optional[str] = None,
    ) -> Optional[RefreshSessionModel]:
        current_time = int(time.time())

        with get_db() as db:
            query = (
                db.query(RefreshSession)
                .filter(RefreshSession.id == session_id)
                .filter(RefreshSession.revoked_at.is_(None))
                .filter(RefreshSession.expires_at > current_time)
            )

            if current_token_hash is not None:
                # Compare-and-swap rotation. If another request already won the
                # refresh race, return None instead of overwriting its token.
                query = query.filter(RefreshSession.token_hash == current_token_hash)

            updated = query.update(
                {
                    "token_hash": token_hash,
                    "expires_at": expires_at,
                    "updated_at": current_time,
                },
                synchronize_session=False,
            )
            if updated == 0:
                return None

            db.commit()

            refresh_session = (
                db.query(RefreshSession).filter(RefreshSession.id == session_id).first()
            )
            if not refresh_session:
                return None

            db.refresh(refresh_session)

            return RefreshSessionModel.model_validate(refresh_session)

    def revoke_session_by_id(self, session_id: str) -> bool:
        current_time = int(time.time())

        with get_db() as db:
            refresh_session = (
                db.query(RefreshSession).filter(RefreshSession.id == session_id).first()
            )
            if not refresh_session:
                return False

            refresh_session.revoked_at = current_time
            refresh_session.updated_at = current_time

            db.commit()
            return True

    def revoke_sessions_by_user_id(self, user_id: str) -> int:
        current_time = int(time.time())

        with get_db() as db:
            result = (
                db.query(RefreshSession)
                .filter(RefreshSession.user_id == user_id)
                .filter(RefreshSession.revoked_at.is_(None))
                .update(
                    {"revoked_at": current_time, "updated_at": current_time},
                    synchronize_session=False,
                )
            )
            db.commit()
            return result

    def delete_expired_sessions(self, current_time: Optional[int] = None) -> int:
        """Delete sessions that expired at or before ``current_time``.

        The optional cutoff keeps the helper deterministic for tests and future
        maintenance jobs that may need to evaluate expiry at a fixed timestamp.
        """
        expiry_time = current_time if current_time is not None else int(time.time())

        with get_db() as db:
            result = (
                db.query(RefreshSession)
                .filter(RefreshSession.expires_at <= expiry_time)
                .delete(synchronize_session=False)
            )
            db.commit()
            return result


RefreshSessions = RefreshSessionsTable()
