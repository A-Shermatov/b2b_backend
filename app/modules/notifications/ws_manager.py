import json
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class NotificationConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        """Register an already-accepted websocket for the user."""
        self._connections[user_id].add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        sockets = self._connections.get(user_id)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            del self._connections[user_id]

    def is_any_online(self, user_ids: list[int]) -> bool:
        return any(bool(self._connections.get(user_id)) for user_id in user_ids)

    async def broadcast_to_users(self, user_ids: list[int], payload: dict) -> None:
        message = json.dumps(payload, default=str)
        for user_id in user_ids:
            for websocket in list(self._connections.get(user_id, set())):
                try:
                    await websocket.send_text(message)
                except Exception:
                    logger.debug("WS send failed for user %s", user_id, exc_info=True)
                    self.disconnect(user_id, websocket)


    async def end_sessions(self, user_id: int, reason: str, message: str) -> None:
        """Tell every open tab of the user that the session is over, then close it.

        Tokens are already invalid by then; this only makes the tab react at once
        instead of on its next request.
        """
        payload = json.dumps({"event": "session_ended", "data": {"reason": reason, "message": message}})
        for websocket in list(self._connections.get(user_id, set())):
            try:
                await websocket.send_text(payload)
                await websocket.close(code=4401)
            except Exception:
                logger.debug("WS close failed for user %s", user_id, exc_info=True)
            self.disconnect(user_id, websocket)


notification_ws_manager = NotificationConnectionManager()
