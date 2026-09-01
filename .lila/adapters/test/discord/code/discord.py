"""``test/discord@1/channel`` — one Discord channel a bot can post to.

Outbound only: posting is ``POST /channels/{id}/messages`` with a bot token, so it is
``urllib.request`` and nothing else — stdlib, in-process. Receiving is a websocket
gateway, which is the MCP boundary's problem, not a bigger version of this file.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TypedDict

from lila.ext import Secret, ToolError, resource, tool

API_BASE = "https://discord.com/api/v10"
TIMEOUT = 15.0

# Discord rejects a longer message outright. Truncating means a notification always
# lands; splitting into several posts is the alternative, and is worth it only once a
# consumer actually needs the tail — it turns one call into N with partial failures.
MAX_CONTENT = 2000
ELLIPSIS = "…"


@resource
@dataclass(frozen=True)
class Channel:
    """One channel and the bot that posts to it. These fields are the config.toml shape."""

    token: Secret  # a bot token, sent as ``Authorization: Bot …``
    channel_id: str  # right-click a channel with developer mode on → Copy Channel ID
    api_base: str = API_BASE

    def post(self, path: str, payload: dict[str, str]) -> dict[str, object]:
        """POST JSON to the API and return the decoded response.

        Raises:
            ToolError: the request failed, or Discord rejected it.
        """
        request = urllib.request.Request(
            f"{self.api_base}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "DiscordBot (https://github.com/lila, 1)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                decoded = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ToolError(
                f"discord {path} failed: {exc.code} {exc.read().decode()[:200]}"
            ) from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise ToolError(f"discord {path} failed: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ToolError(f"discord {path} returned {type(decoded).__name__}, not an object")
        return decoded


class Posted(TypedDict):
    """What ``post_message`` returns."""

    id: str
    channel_id: str
    truncated: bool  # the content hit the 2000-character cap and lost its tail


@tool
def post_message(channel: Channel, content: str) -> Posted:
    """Post one message to the channel, truncated to Discord's 2000-character cap.

    Raises:
        ToolError: the content is empty, or Discord rejected the post.
    """
    if not content.strip():
        raise ToolError("cannot post an empty message")
    truncated = len(content) > MAX_CONTENT
    if truncated:
        content = content[: MAX_CONTENT - len(ELLIPSIS)] + ELLIPSIS
    message = channel.post(f"/channels/{channel.channel_id}/messages", {"content": content})
    return {
        "id": str(message.get("id", "")),
        "channel_id": str(message.get("channel_id", channel.channel_id)),
        "truncated": truncated,
    }
