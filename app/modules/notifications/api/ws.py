import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security_core import decode_token
from app.database import pool
from app.modules.notifications.ws_manager import notification_ws_manager

logger = logging.getLogger(__name__)

ws_router = APIRouter()


async def _user_id_from_token(token: str) -> int | None:
    try:
        payload = decode_token(token)
    except ValueError:
        return None
    if payload.get("type") != "access":
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        return None
    if not pool.ready:
        await pool.init()
    async with pool.connection() as conn:
        row = await conn.fetchrow("SELECT id FROM users WHERE id = $1", user_id)
    return row["id"] if row else None


@ws_router.websocket("/notifications")
async def notifications_websocket(websocket: WebSocket) -> None:
    await websocket.accept()

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return

    user_id = await _user_id_from_token(token)
    if not user_id:
        await websocket.close(code=4401)
        return

    await notification_ws_manager.connect(user_id, websocket)
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_text('{"event":"ping"}')
                continue
            if message.strip().lower() == "pong":
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WS session ended for user %s", user_id, exc_info=True)
    finally:
        notification_ws_manager.disconnect(user_id, websocket)
