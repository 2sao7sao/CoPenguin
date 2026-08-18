"""Optional Feishu WebSocket transport backed by the official lark-oapi SDK."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import Any

from .config import Settings
from .feishu import FeishuPayloadError, FeishuWebhookService

logger = logging.getLogger(__name__)


class _AsyncServiceBridge:
    """Keep service coroutines on one loop while the SDK owns its own loop."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="copenguin-feishu-service-loop",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=5)
        if not self._ready.is_set():
            raise RuntimeError("Feishu service loop did not start")

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def submit(self, coroutine: Coroutine[Any, Any, dict[str, Any]]) -> Future[dict[str, Any]]:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        future.add_done_callback(self._log_failure)
        return future

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if not self._loop.is_closed():
            self._loop.close()

    def _log_failure(self, future: Future[dict[str, Any]]) -> None:
        try:
            future.result()
        except Exception:  # noqa: BLE001 - callback boundary must remain alive
            logger.exception("Feishu long-connection event handling failed")


class FeishuLongConnectionRunner:
    """Receive messages and approval-card callbacks without a public webhook."""

    def __init__(self, *, settings: Settings, service: FeishuWebhookService) -> None:
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise ValueError("FEISHU_APP_ID and FEISHU_APP_SECRET are required")
        self._settings = settings
        self._service = service

    def start(self) -> None:
        try:
            import lark_oapi as lark
            from lark_oapi import ws
            from lark_oapi.event.callback.model.p2_card_action_trigger import (
                P2CardActionTriggerResponse,
            )
        except ImportError as exc:
            raise RuntimeError(
                'Feishu long connection requires `python -m pip install ".[feishu]"`'
            ) from exc

        bridge = _AsyncServiceBridge()

        def payload(value: Any) -> dict[str, Any]:
            raw = lark.JSON.marshal(value)
            decoded = json.loads(raw or "null")
            if not isinstance(decoded, dict):
                raise FeishuPayloadError("Official SDK produced a non-object event payload")
            return decoded

        def on_message(data: Any) -> None:
            bridge.submit(
                self._service.handle_payload(
                    payload(data),
                    trusted_long_connection=True,
                )
            )

        def on_card_action(data: Any) -> Any:
            bridge.submit(
                self._service.handle_card_action(
                    payload(data),
                    trusted_long_connection=True,
                )
            )
            return P2CardActionTriggerResponse(
                {"toast": {"type": "info", "content": "CoPenguin is recording the decision."}}
            )

        dispatcher = (
            lark.EventDispatcherHandler.builder(
                self._settings.feishu_encrypt_key,
                self._settings.feishu_verification_token,
            )
            .register_p2_im_message_receive_v1(on_message)
            .register_p2_card_action_trigger(on_card_action)
            .build()
        )
        client = ws.Client(
            self._settings.feishu_app_id,
            self._settings.feishu_app_secret,
            event_handler=dispatcher,
        )
        try:
            client.start()
        finally:
            bridge.close()
