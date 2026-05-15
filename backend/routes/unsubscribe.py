import os
from datetime import datetime, timezone
from fastapi import APIRouter
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)


@router.get("/unsubscribe")
async def confirm_unsubscribe(token: str):
    """
    Confirms an unsubscribe request sent via SMS STOP flow.
    Looks up the token, sets users.paused=True, marks token used.
    """
    if not token:
        return {"success": False, "message": "Link expired or invalid"}

    try:
        row = (
            supabase.table("phone_link_tokens")
            .select("id, user_id, used, expires_at")
            .eq("token", token)
            .eq("used", False)
            .execute()
        )
    except Exception:
        return {"success": False, "message": "Link expired or invalid"}

    if not row.data:
        return {"success": False, "message": "Link expired or invalid"}

    entry = row.data[0]

    expires_at_str = entry["expires_at"]
    # Supabase returns ISO 8601 with timezone
    expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires_at:
        return {"success": False, "message": "Link expired or invalid"}

    user_id = entry["user_id"]

    try:
        supabase.table("users").update({"paused": True}).eq("id", user_id).execute()
        supabase.table("phone_link_tokens").update({"used": True}).eq("id", entry["id"]).execute()
    except Exception:
        return {"success": False, "message": "Something went wrong. Email support@stackd.chat"}

    return {"success": True, "message": "You have been unsubscribed"}
