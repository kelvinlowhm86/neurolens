"""A public YouTube video's facts from the Data API (M3j §3). Standard library only.

The web tier calls `lookup` for a link the user gives it. A reply that is not a video, or a lookup
that cannot be made, is told apart: no items is `None`; every failure is `LookupFailed`, whose
message never holds the API key or the request's URL.
"""

import http.client
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

API_URL = "https://www.googleapis.com/youtube/v3/videos"
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


@dataclass(frozen=True)
class VideoFacts:
    published_at: datetime
    duration_s: int
    views: int | None


class LookupFailed(Exception):
    """The video could not be looked up; the message never contains the key or the URL."""


def _duration_seconds(text):
    match = _DURATION.fullmatch(text) if isinstance(text, str) else None
    if not match:
        raise ValueError("not an ISO 8601 duration")
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _published_at(text):
    if not isinstance(text, str):
        raise ValueError("publishedAt is missing")
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _facts(item):
    if not isinstance(item, dict):
        raise ValueError("the item is not an object")
    snippet = item.get("snippet")
    details = item.get("contentDetails")
    statistics = item.get("statistics") or {}
    if not isinstance(snippet, dict) or not isinstance(details, dict):
        raise ValueError("the reply is missing a field")
    if not isinstance(statistics, dict):
        raise ValueError("statistics is not an object")
    views = statistics.get("viewCount")
    if views is not None:
        views = int(views)
    return VideoFacts(
        published_at=_published_at(snippet.get("publishedAt")),
        duration_s=_duration_seconds(details.get("duration")),
        views=views,
    )


def lookup(video_id, api_key, *, timeout=5):
    """The public video's facts, or None when YouTube has no such item (M3j §3).

    A missing key, a network error, a timeout, a non-200 reply, or a reply of the wrong shape
    raises `LookupFailed`. The message is deliberately generic: it never carries the key or the
    request's URL.
    """
    if not api_key:
        raise LookupFailed("The video lookup has no API key.")
    query = urlencode(
        {
            "part": "snippet,contentDetails,statistics",
            "id": video_id,
            "key": api_key,
        }
    )
    request = urllib.request.Request(f"{API_URL}?{query}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.getcode() if hasattr(response, "getcode") else response.status
            if status != 200:
                raise LookupFailed(f"YouTube answered {status}.")
            body = response.read()
    except LookupFailed:
        raise
    except (urllib.error.URLError, http.client.HTTPException, OSError) as err:
        if isinstance(err, urllib.error.HTTPError):
            raise LookupFailed(f"YouTube answered {err.code}.") from None
        raise LookupFailed(f"The video lookup failed: {type(err).__name__}.") from None

    try:
        reply = json.loads(body)
        items = reply["items"]
        if not isinstance(items, list):
            raise ValueError("items is not a list")
        if not items:
            return None
        if len(items) != 1:
            raise ValueError("more than one item")
        return _facts(items[0])
    except (ValueError, TypeError, KeyError):
        raise LookupFailed("YouTube's reply was not a video.") from None
