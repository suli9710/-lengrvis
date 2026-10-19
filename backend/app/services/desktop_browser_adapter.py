from __future__ import annotations

import asyncio

from app.services.browser_execution_grants import issue_browser_command
from app.services.browser_host_bridge_service import browser_host_bridge_hub


class DesktopBrowserActivityAdapter:
    def _request(self, session, action, context, operation):
        authorization = issue_browser_command(session, action, context, operation=operation)
        return asyncio.run(browser_host_bridge_hub.request_authorized_action(authorization))

    def _ensure_open(self, session, action, context):
        status = browser_host_bridge_hub.status()
        if not status["connected"]:
            raise RuntimeError("请打开桌面应用以继续原浏览器会话。")
        sessions = (status.get("snapshot") or {}).get("sessions") or []
        existing = next((item for item in sessions if item.get("id") == session.id), None)
        if existing:
            if existing.get("task_id") != session.task_id:
                raise ValueError("Browser session belongs to another task.")
            return
        if session.last_observation is not None:
            raise RuntimeError("原浏览器会话已丢失，请重新观察和确认后接续。")
        opened = self._request(
            session, {"kind": "open", "url": action.get("url") or session.current_url}, context, "open"
        )
        if not opened.get("ok"):
            raise RuntimeError(opened.get("error") or "Could not open persistent browser session")

    def inspect(self, session, action, context):
        self._ensure_open(session, action, context)
        result = self._request(session, action, context, "inspect")
        if not result.get("ok") or not result.get("inspection"):
            raise ValueError(result.get("error") or "Browser target could not be inspected")
        return {
            "kind": "browser_control",
            "identity_version": "browser-control/v1",
            "session_id": session.id,
            **result["inspection"],
        }

    def perform(self, session, action, context):
        self._ensure_open(session, action, context)
        result = self._request(session, action, context, "act")
        hosted = result.get("session") or {}
        observed = result.get("observation") or {}
        if not isinstance(observed, dict):
            observed = {}
        return {
            "ok": result.get("ok", False),
            "error": result.get("error") or "",
            "url": hosted.get("current_url") or session.current_url,
            "title": hosted.get("title") or "",
            "text": observed.get("text") or "",
            "links": observed.get("links") or [],
            "controls": observed.get("controls") or [],
            "extracted": observed.get("extracted") or {},
            "elements": observed.get("elements") or [],
            "truncated": bool(observed.get("truncated")),
            "present": observed.get("present"),
            "observed_at": observed.get("observed_at"),
            "adapter": "desktop_browser_host",
            "commit_state": result.get("commit_state"),
            "screenshot_url": (result.get("event") or {}).get("screenshot_url"),
        }


async def request_task_observation(matter, session_id: str, *, selectors: dict | None = None) -> dict:
    """Read an owned retained tab, including while no active execution is bound."""
    from app.services.browser_execution_grants import issue_matter_observation
    from app.services.butler_scopes import origin_of

    snapshot = browser_host_bridge_hub.status()
    existing = next(
        (item for item in (snapshot.get("snapshot") or {}).get("sessions", []) if item.get("id") == session_id), None
    )
    if not snapshot.get("connected") or existing is None:
        raise ValueError("The owned browser session is unavailable.")
    command = issue_matter_observation(matter, existing, selectors=selectors)
    result = await browser_host_bridge_hub.request_authorized_action(command)
    returned = result.get("session") or {}
    if (
        not result.get("ok")
        or returned.get("task_id") != existing.get("task_id")
        or returned.get("account_id", "") != (existing.get("account_id") or "")
    ):
        raise ValueError("Browser observation failed or account changed.")
    observation = result.get("observation") or {}
    if origin_of(observation.get("url", "")) != origin_of(existing["current_url"]):
        raise ValueError("Browser observation changed origin.")
    return observation
