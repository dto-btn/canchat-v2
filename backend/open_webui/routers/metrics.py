from open_webui.constants import ERROR_MESSAGES
from open_webui.models.users import Users
from open_webui.models.message_metrics import MessageMetrics
from open_webui.models.export_logs import ExportLogs, ExportLogForm
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
    Response,
)
from fastapi.responses import StreamingResponse
import logging
import csv
import io
import time
from typing import Optional

from open_webui.utils.auth import get_metrics_user
from open_webui.env import SRC_LOG_LEVELS
from datetime import datetime, timezone

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["METRICS"])

router = APIRouter()

############################
# GetTotalPrompts
############################


@router.get("/prompts")
async def get_total_prompts(domain: str = None, user=Depends(get_metrics_user)):
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    total_prompts = (
        MessageMetrics.get_messages_number(domain)
        if domain
        else MessageMetrics.get_messages_number()
    )

    # Return 0 instead of raising a 404 error
    return {"total_prompts": total_prompts or 0}


############################
# GetDomains
############################


@router.get("/domains")
async def get_domains(user=Depends(get_metrics_user)):
    if user.role not in ["admin", "analyst", "global_analyst"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # If user is analyst, only return their domain
    if user.role == "analyst":
        return {"domains": [user.domain] if user.domain else []}

    # For admins and global_analysts, return all domains
    domains = MessageMetrics.get_domains() or []
    return {"domains": domains}


############################
# GetDailyUsers
############################


@router.get("/daily/prompts")
async def get_daily_prompts_number(domain: str = None, user=Depends(get_metrics_user)):
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    total_daily_prompts = (
        MessageMetrics.get_daily_messages_number(domain=domain)
        if domain
        else MessageMetrics.get_daily_messages_number()
    )
    return {"total_daily_prompts": total_daily_prompts}


############################
# GetMcpProcesses
############################


@router.get("/mcp-processes")
async def get_mcp_processes(user=Depends(get_metrics_user)):
    """Return the distinct MCP toggle/process names that have been logged in message_metrics."""
    if user.role not in ["admin", "analyst", "global_analyst"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    processes = MessageMetrics.get_mcp_processes() or []
    return {"mcp_processes": processes}


############################
# GetTotalTokens
############################


@router.get("/tokens")
async def get_total_tokens(
    domain: str = None,
    start_date: str = None,
    end_date: str = None,
    mcp_tool: str = None,
    user=Depends(get_metrics_user),
):
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    # Convert dates to timestamps if provided
    start_timestamp = None
    end_timestamp = None

    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            start_timestamp = int(start_dt.timestamp())
        except ValueError:
            pass

    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            # Set end_timestamp to the end of the specified day (23:59:59 UTC)
            end_of_day = end_dt.replace(hour=23, minute=59, second=59)
            end_timestamp = int(end_of_day.timestamp())
        except ValueError:
            pass

    total_tokens = MessageMetrics.get_message_tokens_sum(
        domain=domain,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        mcp_tool=mcp_tool,
    )

    return {"total_tokens": total_tokens}


############################
# GetDailyTokens
############################


@router.get("/daily/tokens")
async def get_daily_tokens(
    domain: str = None, mcp_tool: str = None, user=Depends(get_metrics_user)
):
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    total_daily_tokens = MessageMetrics.get_daily_message_tokens_sum(
        domain=domain, mcp_tool=mcp_tool
    )
    return {"total_daily_tokens": total_daily_tokens}


############################
# GetHistoricalPrompts
############################


@router.get("/historical/prompts")
async def get_historical_prompts(
    days: int = 7, domain: str = None, user=Depends(get_metrics_user)
):
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    # Handle both None and empty string for domain
    if domain == "":
        domain = None

    historical_data = MessageMetrics.get_historical_messages_data(days, domain)

    return {"historical_prompts": historical_data}


############################
# GetHistoricalTokens
############################


@router.get("/historical/tokens")
async def get_historical_tokens(
    days: int = 7,
    domain: str = None,
    mcp_tool: str = None,
    user=Depends(get_metrics_user),
):
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    # Handle both None and empty string for domain / mcp_tool
    if domain == "":
        domain = None
    if mcp_tool == "":
        mcp_tool = None

    historical_data = MessageMetrics.get_historical_tokens_data(days, domain, mcp_tool)

    return {"historical_tokens": historical_data}


############################
# GetModels
############################


# Active = a stored name that matches a model in the chat dropdown.
# Archived = any other stored name, one entry each.
# TODO: group by the planned model_family / model_generation columns once they exist.


def _match_live_name(used_id: str, live: dict) -> Optional[str]:
    """Id of the chat model whose id or display name matches, ignoring case."""
    name = used_id.casefold()
    for live_id, model in live.items():
        if name in (live_id.casefold(), str(model.get("name") or "").casefold()):
            return live_id
    return None


def _model_key(used_id: str, live: dict) -> str:
    """Dropdown entry for a stored name: the chat model id, or the name itself."""
    return _match_live_name(used_id, live) or used_id


def _is_hidden(model: dict) -> bool:
    """Hidden models aren't in the chat dropdown, so they count as archived."""
    meta = (model.get("info") or {}).get("meta") or {}
    return bool(meta.get("hidden"))


_LIVE_MODELS_RETRY_SECONDS = 60
_live_models_retry_at = 0.0


async def _get_live_models(request: Request) -> dict:
    """Models in the chat dropdown, keyed by id (no arena or hidden models)."""
    global _live_models_retry_at

    live_models = request.app.state.MODELS
    # Loading calls every provider, so retry at most once a minute if it comes back empty
    if not live_models and time.monotonic() >= _live_models_retry_at:
        # Set before awaiting so concurrent requests don't load again
        _live_models_retry_at = time.monotonic() + _LIVE_MODELS_RETRY_SECONDS
        try:
            # Imported here to avoid a circular import at module load
            from open_webui.utils.models import get_all_models

            await get_all_models(request)
            live_models = request.app.state.MODELS
        except Exception as e:
            log.warning(f"Could not load live models for metrics: {e}")
            live_models = {}

    return {
        model_id: model
        for model_id, model in (live_models or {}).items()
        if not model.get("arena") and not _is_hidden(model)
    }


async def _resolve_model_filter(
    request: Request, model: Optional[str]
) -> Optional[list[str]]:
    """Stored names to count for a dropdown selection.

    Archived: the name itself. Active: the chat model's id and display name in
    any case (e.g. "GPT 4o", "gpt 4o").
    """
    if not model:
        return None
    live = await _get_live_models(request)
    if model not in live:
        return [model]
    # Narrow in SQL by prefix, then keep exact matches only (drops "GPT 4o mini")
    prefixes = [model]
    if live[model].get("name"):
        prefixes.append(live[model]["name"])
    names = [
        name
        for name in MessageMetrics.get_prompt_models(prefixes=prefixes)
        if _match_live_name(name, live) == model
    ]
    return names or [model]


@router.get("/models")
async def get_models(request: Request, user=Depends(get_metrics_user)):
    """Models users prompted with, split into active and archived.

    Tool-use (CrewAI) rows are left out; the Tokens tab covers them. Analysts
    see every model; the metric queries are domain-filtered.

    NOTE: `models` (every stored name, kept for older callers) no longer includes
    tool-use names like "azure/gpt-4o" or "crewai" (CANCHAT-5).
    """
    used_models = MessageMetrics.get_prompt_models() or []
    live = await _get_live_models(request)

    keys = sorted({_model_key(name, live) for name in used_models})
    active_models = [
        {"id": key, "name": live[key].get("name") or key} for key in keys if key in live
    ]
    archived_models = [{"id": key, "name": key} for key in keys if key not in live]

    active_models.sort(key=lambda m: m["name"].lower())
    archived_models.sort(key=lambda m: m["name"].lower())

    return {
        "models": used_models,
        "active_models": active_models,
        "archived_models": archived_models,
    }


############################
# GetModelPrompts
############################


@router.get("/models/prompts")
async def get_model_prompts(
    request: Request,
    model: str = None,
    domain: str = None,
    user=Depends(get_metrics_user),
):
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    models = await _resolve_model_filter(request, model)
    total_prompts = MessageMetrics.get_messages_number(domain, models)
    return {"total_prompts": total_prompts or 0}


############################
# GetModelDailyPrompts
############################


@router.get("/models/daily/prompts")
async def get_model_daily_prompts(
    request: Request,
    model: str = None,
    domain: str = None,
    user=Depends(get_metrics_user),
):
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    models = await _resolve_model_filter(request, model)
    total_daily_prompts = MessageMetrics.get_daily_messages_number(
        domain=domain, model=models
    )
    return {"total_daily_prompts": total_daily_prompts}


############################
# GetModelHistoricalPrompts
############################


@router.get("/models/historical/prompts")
async def get_model_historical_prompts(
    request: Request,
    days: int = 7,
    model: str = None,
    domain: str = None,
    user=Depends(get_metrics_user),
):
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    # Handle both None and empty string
    if domain == "":
        domain = None
    if model == "":
        model = None

    models = await _resolve_model_filter(request, model)
    historical_data = MessageMetrics.get_historical_messages_data(days, domain, models)

    return {"historical_prompts": historical_data}


############################
# GetHistoricalDailyUsers
############################


@router.get("/historical/users/daily")
async def get_historical_daily_users(
    request: Request,
    days: int = 7,
    model: str = None,
    domain: str = None,
    user=Depends(get_metrics_user),
):
    if user.role in ["admin", "global_analyst"]:
        domain_to_use = domain if domain != "" else None
    elif user.role == "analyst":
        domain_to_use = user.domain
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    models = await _resolve_model_filter(request, model)
    historical_daily_data = MessageMetrics.get_historical_daily_users(
        days, domain_to_use, models
    )

    return {"historical_daily_users": historical_daily_data}


############################
# Date Range Metrics
############################

# NOTE: unused. The frontend fetched this but never showed it, so the call was
# removed (CANCHAT-5). Kept for a possible date-range summary panel.


@router.get("/range/users")
async def get_range_metrics(
    request: Request,
    start_date: str,
    end_date: str,
    domain: str = None,
    model: str = None,
    user=Depends(get_metrics_user),
):
    """Get metrics for a specific date range"""
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    try:
        # Convert dates to timestamps
        start_timestamp = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        end_timestamp = (
            int(datetime.strptime(end_date, "%Y-%m-%d").timestamp()) + 86400
        )  # End of day

        # Calculate total number of days in range for averaging
        days_in_range = (end_timestamp - start_timestamp) // 86400

        # Get metrics for date range
        users_data = Users.get_range_metrics(start_timestamp, end_timestamp, domain)
        models = await _resolve_model_filter(request, model)
        prompts_data = MessageMetrics.get_range_metrics(
            start_timestamp, end_timestamp, domain, models
        )

        # Calculate averages
        avg_daily_users = (
            users_data["total_users"] / days_in_range if days_in_range > 0 else 0
        )
        avg_daily_prompts = (
            prompts_data["total_prompts"] / days_in_range if days_in_range > 0 else 0
        )
        avg_tokens_per_prompt = (
            prompts_data["total_tokens"] / prompts_data["total_prompts"]
            if prompts_data["total_prompts"] > 0
            else 0
        )

        # Calculate growth rates if previous period data is available
        prev_start = start_timestamp - (end_timestamp - start_timestamp)
        prev_end = start_timestamp
        prev_users_data = Users.get_range_metrics(prev_start, prev_end, domain)
        # Same names as the current period, so growth compares like with like
        prev_prompts_data = MessageMetrics.get_range_metrics(
            prev_start, prev_end, domain, models
        )

        user_growth = (
            (
                (users_data["total_users"] - prev_users_data["total_users"])
                / prev_users_data["total_users"]
            )
            * 100
            if prev_users_data["total_users"] > 0
            else 0
        )
        prompt_growth = (
            (
                (prompts_data["total_prompts"] - prev_prompts_data["total_prompts"])
                / prev_prompts_data["total_prompts"]
            )
            * 100
            if prev_prompts_data["total_prompts"] > 0
            else 0
        )

        return {
            "period": {
                "start_date": start_date,
                "end_date": end_date,
                "days": days_in_range,
            },
            "metrics": {
                "total_users": users_data["total_users"],
                "total_prompts": prompts_data["total_prompts"],
                "total_tokens": prompts_data["total_tokens"],
                "active_users": users_data["active_users"],
            },
            "averages": {
                "avg_daily_users": round(avg_daily_users, 2),
                "avg_daily_prompts": round(avg_daily_prompts, 2),
                "avg_tokens_per_prompt": round(avg_tokens_per_prompt, 2),
                "avg_prompts_per_user": (
                    round(prompts_data["total_prompts"] / users_data["active_users"], 2)
                    if users_data["active_users"] > 0
                    else 0
                ),
            },
            "growth": {
                "user_growth_percent": round(user_growth, 2),
                "prompt_growth_percent": round(prompt_growth, 2),
            },
        }
    except Exception as e:
        log.exception(f"Error getting range metrics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get range metrics: {str(e)}",
        )


############################
# GetInterPromptLatencyHistogram
############################


@router.get("/inter-prompt-latency")
async def get_inter_prompt_latency_histogram(
    domain: str = None, model: str = None, user=Depends(get_metrics_user)
):
    """
    Get inter-prompt latency histogram for user behavior analysis.

    Returns the time between user prompts in a session (chat), presented as
    histogram data with logarithmic bins from 0-2s up to 1024-2048s.

    Only considers second or subsequent prompts in a chat session.
    """
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    try:
        histogram_data = MessageMetrics.get_inter_prompt_latency_histogram(
            domain, model
        )
        return histogram_data
    except Exception as e:
        log.exception(f"Error getting inter-prompt latency histogram: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get inter-prompt latency histogram: {str(e)}",
        )


############################
# GetInterPromptLatencyHistogram
############################


@router.get("/inter-prompt-latency")
async def get_inter_prompt_latency_histogram(
    domain: str = None, model: str = None, user=Depends(get_metrics_user)
):
    """
    Get inter-prompt latency histogram for user behavior analysis.

    This endpoint returns a histogram showing the time between user prompts
    in chat sessions. Only considers second or subsequent prompts in a session.
    Data is presented with logarithmic time bins from 0-2s up to 1024-2048s.
    """
    # For analyst role, enforce domain restriction
    if user.role == "analyst":
        # Force domain to user's domain for analysts
        domain = user.domain

    # Admin and global_analyst can see all domains or filter by domain

    histogram_data = MessageMetrics.get_inter_prompt_latency_histogram(domain, model)

    return {
        "histogram": histogram_data,
        "description": "Time between user prompts in chat sessions (inter-prompt latency)",
    }


############################
# Export Data
############################


@router.post("/export")
async def export_metrics_data(
    start_date: str, end_date: str, domain: str = None, user=Depends(get_metrics_user)
):
    """
    Export metrics data as CSV for a specific date range.
    Accessible by users with admin, global_analyst, or analyst roles.
    """
    try:
        # Convert dates to timestamps
        start_timestamp = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        end_timestamp = (
            int(datetime.strptime(end_date, "%Y-%m-%d").timestamp()) + 86400
        )  # End of day

        # Validate date range (max 90 days)
        # Calculate days based on the actual date difference, not including the end-of-day addition
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")
        date_range_days = (
            end_date_obj - start_date_obj
        ).days + 1  # +1 to include both start and end days

        if date_range_days > 90:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Date range cannot exceed 90 days",
            )

        # For analyst role, enforce domain restriction to their own domain
        if user.role == "analyst":
            domain = user.domain

        # Get metrics data for the date range
        metrics_data = MessageMetrics.get_export_data(
            start_timestamp, end_timestamp, domain
        )

        if not metrics_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No data found for the specified date range",
            )

        # Create CSV content
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header - include all available fields
        headers = [
            "Export Date",
            "User Domain",
            "Model",
            "User ID",
            "Chat ID",
            "Prompt Tokens",
            "Completion Tokens",
            "Total Tokens",
            "Message Created At",
            "Message Timestamp",
            "Metrics Record ID",
        ]
        writer.writerow(headers)

        # Write data rows
        export_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for row in metrics_data:
            # Convert timestamp to readable date
            message_date_str = datetime.fromtimestamp(row["created_at"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            writer.writerow(
                [
                    export_timestamp,  # Export Date - when this export was performed
                    row["user_domain"],
                    row["model"],
                    row["user_id"],
                    row["chat_id"] or "",
                    row["prompt_tokens"],
                    row["completion_tokens"],
                    row["total_tokens"],
                    message_date_str,  # Message Timestamp - when the message was created
                    row["created_at"],  # Raw timestamp
                    row["id"],  # Metrics Record ID - the message_metrics table row ID
                ]
            )

        csv_content = output.getvalue()
        output.close()

        # Calculate file size and row count
        file_size = len(csv_content.encode("utf-8"))
        row_count = len(metrics_data)

        # Log the export
        export_log_form = ExportLogForm(
            user_id=user.id,
            email_domain=user.domain or "unknown",
            file_size=file_size,
            row_count=row_count,
            date_range_start=start_timestamp,
            date_range_end=end_timestamp,
        )

        ExportLogs.insert_new_export_log(export_log_form)

        # Generate filename
        filename = f"metrics_export_{start_date}_to_{end_date}.csv"

        # Return CSV as streaming response
        return StreamingResponse(
            io.BytesIO(csv_content.encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid date format. Use YYYY-MM-DD: {str(e)}",
        )
    except Exception as e:
        log.exception(f"Error exporting metrics data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export data: {str(e)}",
        )


@router.get("/export/logs")
async def get_export_logs(user=Depends(get_metrics_user)):
    """
    Get export logs. Only accessible by admin users.
    """
    # Only admins can view export logs
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can view export logs",
        )

    try:
        # Admins can see all export logs
        export_logs = ExportLogs.get_all_export_logs()
        return {"export_logs": export_logs}

    except Exception as e:
        log.exception(f"Error getting export logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get export logs: {str(e)}",
        )
