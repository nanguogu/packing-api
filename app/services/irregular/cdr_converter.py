"""Pluggable CDR-to-SVG conversion client."""

from __future__ import annotations

import os

import httpx


def obtain_svg(cdr_bytes: bytes, filename: str, supplied_svg: bytes | None) -> tuple[bytes | None, dict]:
    """Use a supplied SVG or a configured CorelDRAW Windows worker."""
    if supplied_svg:
        return supplied_svg, {"status": "ready", "provider": "uploaded_svg"}
    worker_url = os.getenv("CDR_CONVERTER_URL")
    if not worker_url:
        return None, {
            "status": "conversion_required",
            "provider": None,
            "message": "Configure CDR_CONVERTER_URL or upload a CorelDRAW-exported SVG.",
        }
    try:
        response = httpx.post(
            worker_url.rstrip("/") + "/convert",
            files={"cdr_file": (filename, cdr_bytes, "application/octet-stream")},
            timeout=60,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return None, {
            "status": "converter_unavailable",
            "provider": "coreldraw_windows_worker",
            "message": str(exc),
        }
    return response.content, {"status": "ready", "provider": "coreldraw_windows_worker"}
