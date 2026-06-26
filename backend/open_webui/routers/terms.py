import logging

from open_webui.models.terms import (
    Terms,
    TermsModel,
)
from open_webui.env import SRC_LOG_LEVELS
from open_webui.config import TERMS_VERSION
from fastapi import APIRouter, Depends, HTTPException, status
from open_webui.utils.auth import get_current_user, get_admin_user

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()

############################
# Accept Terms of Use
############################


@router.post("/accept", response_model=TermsModel)
async def accept_terms(user=Depends(get_current_user)):
    try:
        terms = Terms.accept(user.id, version=TERMS_VERSION.value)
    except Exception as e:
        log.exception(f"accept_terms failed for user {user.id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to accept terms of use",
        )
    if not terms:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to accept terms of use",
        )
    return terms


############################
# Get Current User's Terms
############################


@router.get("/status", response_model=TermsModel | None)
async def get_terms_status(user=Depends(get_current_user)):
    try:
        return Terms.get_terms_by_user_id(user.id, version=TERMS_VERSION.value)
    except Exception as e:
        log.exception(f"get_terms_status failed for user {user.id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve terms status",
        )


############################
# Get User Terms (Admin)
############################


@router.get("/user/{user_id}", response_model=TermsModel | None)
async def get_user_terms(user_id: str, user=Depends(get_admin_user)):
    if not user_id or not user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id is required",
        )
    try:
        return Terms.get_terms_by_user_id(user_id, version=TERMS_VERSION.value)
    except Exception as e:
        log.exception(f"get_user_terms failed for user_id {user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve terms for user",
        )
