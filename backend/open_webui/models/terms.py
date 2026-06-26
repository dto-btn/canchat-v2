import logging
import time
import uuid
from typing import Optional

from open_webui.internal.db import get_db
from open_webui.models.base import Base

from open_webui.env import SRC_LOG_LEVELS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Text

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


####################
# Terms DB Schema
####################


class Term(Base):
    __tablename__ = "terms"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    accepted_at = Column(BigInteger, nullable=False)
    version = Column(Text, nullable=False, unique=False)


class TermsModel(BaseModel):
    id: str
    user_id: str
    accepted_at: int
    version: str
    model_config = ConfigDict(from_attributes=True)


####################
# Tables
####################


class TermsTable:
    def accept(self, user_id: str, version: str) -> Optional[TermsModel]:
        try:
            with get_db() as db:
                existing = (
                    db.query(Term).filter_by(user_id=user_id, version=version).first()
                )
                if existing:
                    return TermsModel.model_validate(existing)

                id = str(uuid.uuid4())
                terms = TermsModel(
                    id=id,
                    user_id=user_id,
                    accepted_at=int(time.time()),
                    version=version,
                )
                result = Term(**terms.model_dump())
                db.add(result)
                db.commit()
                db.refresh(result)
                if result:
                    return TermsModel.model_validate(result)
                return None
        except Exception as e:
            log.error(f"Error accepting terms: {e}")
            return None

    def get_terms_by_user_id(self, user_id: str, version: str) -> Optional[TermsModel]:
        try:
            with get_db() as db:
                terms = (
                    db.query(Term).filter_by(user_id=user_id, version=version).first()
                )
                if not terms:
                    return None
                return TermsModel.model_validate(terms)
        except Exception as e:
            log.error(f"Error retrieving terms for user_id {user_id}: {e}")
            return None

    def delete_terms_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Term).filter_by(user_id=user_id).delete()
                db.commit()
                return True
        except Exception as e:
            log.error(f"Error deleting terms for user_id {user_id}: {e}")
            return False


Terms = TermsTable()
