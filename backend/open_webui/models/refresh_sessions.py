from typing import Optional

from open_webui.internal.db import JSONField
from open_webui.models.base import Base

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Text

####################
# Refresh Session DB Schema
####################


class RefreshSession(Base):
    __tablename__ = "refresh_session"

    id = Column(Text, primary_key=True)
    user_id = Column(Text)
    token_hash = Column(Text)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)
    expires_at = Column(BigInteger)
    revoked_at = Column(BigInteger, nullable=True)

    meta = Column(JSONField, nullable=True)


class RefreshSessionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    token_hash: str

    created_at: int
    updated_at: int
    expires_at: int
    revoked_at: Optional[int] = None

    meta: Optional[dict] = None
