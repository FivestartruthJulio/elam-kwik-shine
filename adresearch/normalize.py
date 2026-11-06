"""Turn raw scraper items into flat, consistent ad records.

The scraper mirrors Facebook's internal Ad Library format, which shifts
between snake_case and camelCase over time, so every lookup tolerates both.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone


def _get(d, *keys, default=None):
    """First present, non-empty value among keys (dotted paths allowed)."""
    for key in keys:
        cur = d
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                cur = None
                break
        if cur not in (None, "", [], {}):
            return cur
    return default


def _ts(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        if value > 1e12:  # milliseconds
            value /= 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        if value.isdigit():
            return _ts(int(value))
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


_TEMPLATE = re.compile(r"\{\{[^}]*\}\}")


def _text(v) -> str:
    if isinstance(v, dict):
        v = v.get("text")
    if not isinstance(v, str):
        return ""
    v = _TEMPLATE.sub("", v)  # dynamic-creative placeholders like {{product.name}}
    return re.sub(r"\s+", " ", v).strip()


def normalize(item: dict, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    ad_id = _get(item, "ad_archive_id", "adArchiveID", "adArchiveId", "adId", "id")
    if not ad_id:
        return None
    snap = _get(item, "snapshot", default={}) or {}
    card = _get(snap, "cards.0", default={}) or {}

    start = _ts(_get(item, "start_date", "startDate", "start_date_string", "ad_delivery_start_time"))
    end = _ts(_get(item, "end_date", "endDate", "end_date_string", "ad_delivery_stop_time"))
    is_active = bool(_get(item, "is_active", "isActive", default=end is None or end >= now))

    days = None
    if start:
        until = now if is_active or not end else end
        days = max(0, (until - start).days) + 1

    body = _text(_get(snap, "body")) or _text(_get(card, "body"))
    title = _text(_get(snap, "title")) or _text(_get(card, "title"))
    image = _get(
        snap,
        "images.0.original_image_url", "images.0.resized_image_url",
        "videos.0.video_preview_image_url",
        "cards.0.original_image_url", "cards.0.resized_image_url",
        "cards.0.video_preview_image_url",
    )
    video = _get(snap, "videos.0.video_hd_url", "videos.0.video_sd_url",
                 "cards.0.video_hd_url", "cards.0.video_sd_url")
    platforms = _get(item, "publisher_platform", "publisherPlatform", "publisher_platforms", default=[])
    if isinstance(platforms, str):
        platforms = [platforms]

    return {
        "id": str(ad_id),
        "page_id": str(_get(item, "page_id", "pageID", "pageId", default=_get(snap, "page_id", default=""))),
        "page_name": _get(item, "page_name", "pageName", default=_get(snap, "page_name", default="Unknown")),
        "start_date": start.date().isoformat() if start else None,
        "end_date": end.date().isoformat() if end else None,
        "is_active": is_active,
        "days_running": days,
        "variations": int(_get(item, "collation_count", "collationCount", default=1) or 1),
        "body": body,
        "hook": hook_of(body),
        "title": title,
        "cta": _get(snap, "cta_text", "ctaText", default=_get(card, "cta_text", default="")),
        "link_url": _get(snap, "link_url", "linkUrl", default=_get(card, "link_url", default="")),
        "format": (_get(snap, "display_format", "displayFormat", default="") or "").upper(),
        "image": image or "",
        "video": video or "",
        "platforms": [str(p).upper() for p in platforms],
        "library_url": f"https://www.facebook.com/ads/library/?id={ad_id}",
    }


def hook_of(body: str, max_len: int = 140) -> str:
    """The opening line of the ad: what stops the scroll."""
    if not body:
        return ""
    # Take whole sentences until the hook is long enough to mean something
    # (so "Watch Dr." or "Hi!" don't count as a hook on their own).
    first = ""
    for sentence in re.split(r"(?<=[.!?])\s+", body):
        first = f"{first} {sentence}".strip()
        if len(first) >= 25:
            break
    return first if len(first) <= max_len else first[: max_len - 1].rstrip() + "…"
