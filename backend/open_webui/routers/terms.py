import logging

from fastapi import APIRouter, Depends, HTTPException, status

from open_webui.env import SRC_LOG_LEVELS
from open_webui.models.terms import Terms, TermsModel
from open_webui.utils.auth import get_admin_user, get_current_user

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()

############################
# Accept Terms of Use
############################


@router.post("/accept", response_model=TermsModel)
async def accept_terms(terms_version: str, user=Depends(get_current_user)):
    if not terms_version or not terms_version.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="terms_version is required",
        )
    try:
        terms = Terms.accept(user.id, terms_version)
    except Exception:
        log.error(f"accept_terms failed for user {user.id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to accept terms of use",
        )
    if not terms:
        log.error(f"accept_terms returned None for user {user.id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to accept terms of use",
        )
    return terms


############################
# Get Current User's Terms
############################


@router.get("/status/{terms_version}", response_model=TermsModel | None)
async def get_terms_status(terms_version: str, user=Depends(get_current_user)):
    if not terms_version or not terms_version.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="terms_version is required",
        )
    try:
        return Terms.get_terms_by_user_id(user.id, terms_version)
    except Exception:
        log.error(f"get_terms_status failed for user {user.id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve terms status",
        )


############################
# Get User Terms (Admin)
############################


@router.get("/user/{user_id}/{terms_version}", response_model=TermsModel | None)
async def get_user_terms(
    user_id: str, terms_version: str, user=Depends(get_admin_user)
):
    if not user_id or not user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id is required",
        )
    if not terms_version or not terms_version.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="terms_version is required",
        )
    try:
        return Terms.get_terms_by_user_id(user_id, terms_version)
    except Exception:
        log.error(f"get_user_terms failed for user_id {user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve terms for user",
        )
