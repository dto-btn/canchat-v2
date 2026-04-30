#!/usr/bin/env python3
"""
Seed synthetic chats for cleanup/batching tests.

Usage examples:
  python backend/scripts/generate_test_chats.py
  python backend/scripts/generate_test_chats.py --count 200 --age-days 45
  python backend/scripts/generate_test_chats.py --user-id <user_id> --count 500
  python backend/scripts/generate_test_chats.py --list-users
  python backend/scripts/generate_test_chats.py --user-email admin@canchat.ca --count 50
"""

from __future__ import annotations

import argparse
import sys
import time
import types
from pathlib import Path


def _bootstrap_path() -> None:
    """Ensure backend sources are importable when executing this script directly."""
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


_bootstrap_path()

sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))


def parse_args() -> argparse.Namespace:
    """Parse CLI options for synthetic chat generation."""
    parser = argparse.ArgumentParser(
        description="Generate synthetic chats for an existing Open WebUI user.",
        epilog=(
            "Notes:\n"
            "- user_id is the internal DB user primary key (not email, not display name).\n"
            "- Use --list-users to discover user IDs.\n"
            "- Use --user-email for convenience if you know the account email."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--count", type=int, default=200, help="Number of chats to create"
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Target internal user ID (DB primary key).",
    )
    parser.add_argument(
        "--user-email",
        type=str,
        default=None,
        help="Target user by email instead of user ID.",
    )
    parser.add_argument(
        "--list-users",
        action="store_true",
        help="List available users with id/email/name/role and exit.",
    )
    parser.add_argument(
        "--age-days",
        type=int,
        default=40,
        help="Backdate created_at/updated_at by this many days (default: 40).",
    )
    parser.add_argument(
        "--title-prefix",
        type=str,
        default="Batch Test Chat",
        help="Prefix for generated chat titles.",
    )
    return parser.parse_args()


def list_users() -> int:
    """Print available users for operator discovery and selection."""
    from open_webui.models.users import Users

    users = Users.get_users(limit=200)
    if not users:
        print("No users found in DB.")
        return 1

    print("Available users (id | email | name | role):")
    for user in users:
        print(f"- {user.id} | {user.email} | {user.name} | {user.role}")
    return 0


def resolve_user_id(user_id: str | None, user_email: str | None) -> str:
    """Resolve the target user ID by explicit ID, email, or first DB user fallback."""
    from open_webui.models.users import Users

    if user_id and user_email:
        raise SystemExit("Use either --user-id or --user-email, not both.")

    if user_id:
        user = Users.get_user_by_id(user_id)
        if not user:
            raise SystemExit(f"User not found: {user_id}")
        return user.id

    if user_email:
        user = Users.get_user_by_email(user_email)
        if not user:
            raise SystemExit(f"User not found by email: {user_email}")
        return user.id

    first_user = Users.get_first_user()
    if not first_user:
        raise SystemExit(
            "No users found in DB. Create/login a user first, then rerun with --list-users."
        )
    return first_user.id


def build_chat_payload(index: int, title_prefix: str) -> dict:
    """Build a minimal valid chat payload for test-seeded chat rows."""
    message_id = f"seed-msg-{index}"
    return {
        "title": f"{title_prefix} {index}",
        "history": {
            "messages": {
                message_id: {
                    "id": message_id,
                    "parentId": None,
                    "childrenIds": [],
                    "role": "user",
                    "content": f"Synthetic message {index}",
                    "timestamp": int(time.time()),
                }
            },
            "currentId": message_id,
        },
        "messages": [
            {"id": message_id, "role": "user", "content": f"Synthetic message {index}"}
        ],
    }


def backdate_chats(chat_ids: list[str], age_days: int) -> None:
    """Backdate created/updated timestamps so lifecycle cleanup can target seeded chats."""
    from open_webui.internal.db import get_db
    from open_webui.models.chats import Chat

    if age_days <= 0 or not chat_ids:
        return

    base_ts = int(time.time()) - (age_days * 24 * 60 * 60)
    with get_db() as db:
        for idx, chat_id in enumerate(chat_ids):
            chat = db.get(Chat, chat_id)
            if not chat:
                continue
            ts = base_ts - idx
            chat.created_at = ts
            chat.updated_at = ts
        db.commit()


def main() -> int:
    """Create synthetic chats for one user and optionally backdate them."""
    args = parse_args()
    if args.list_users:
        return list_users()

    if args.count <= 0:
        raise SystemExit("--count must be > 0")

    from open_webui.models.chats import ChatForm, Chats
    from open_webui.models.users import Users

    target_user_id = resolve_user_id(args.user_id, args.user_email)
    target_user = Users.get_user_by_id(target_user_id)
    if not target_user:
        raise SystemExit(f"Resolved user ID not found: {target_user_id}")

    created_chat_ids: list[str] = []
    for i in range(1, args.count + 1):
        payload = build_chat_payload(i, args.title_prefix)
        created = Chats.insert_new_chat(target_user_id, ChatForm(chat=payload))
        if created:
            created_chat_ids.append(created.id)

    backdate_chats(created_chat_ids, args.age_days)

    print(
        f"Created {len(created_chat_ids)}/{args.count} chats for user "
        f"{target_user.name} <{target_user.email}> ({target_user_id}). "
        f"Backdated by {args.age_days} days."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
