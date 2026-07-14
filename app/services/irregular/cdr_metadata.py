"""Read safe, non-geometric metadata and previews from ZIP-based CDR files."""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from xml.etree import ElementTree


CDR_MIMETYPE = "application/x-vnd.corel.zcf.draw.document+zip"


def _text_by_local_name(root: ElementTree.Element, name: str) -> str | None:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == name and element.text:
            return element.text.strip()
    return None


def _all_text_by_local_name(root: ElementTree.Element, name: str) -> list[str]:
    return [
        element.text.strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == name and element.text and element.text.strip()
    ]


def parse_cdr_metadata(content: bytes, filename: str) -> dict:
    """Return metadata available without pretending to decode production curves."""
    if not content:
        raise ValueError("CDR file is empty")
    digest = hashlib.sha256(content).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("Only ZIP/ZCF CorelDRAW files are supported by the metadata reader") from exc

    with archive:
        try:
            mimetype = archive.read("mimetype").decode("utf-8").strip()
        except KeyError as exc:
            raise ValueError("CDR container has no mimetype entry") from exc
        if mimetype != CDR_MIMETYPE:
            raise ValueError(f"Unsupported CDR container type: {mimetype}")

        metadata_root = ElementTree.fromstring(archive.read("META-INF/metadata.xml"))
        text_root = ElementTree.fromstring(archive.read("META-INF/textinfo.xml"))
        preview = archive.read("previews/thumbnail.png") if "previews/thumbnail.png" in archive.namelist() else None

        object_names = []
        layer_names = []
        for bag in text_root.iter():
            local_name = bag.tag.rsplit("}", 1)[-1]
            if local_name == "LayerNames":
                layer_names = _all_text_by_local_name(bag, "li")
            elif local_name == "ObjectNames":
                object_names = _all_text_by_local_name(bag, "li")

        object_stats = {}
        for key in ("Total", "Group", "Curve", "Rect", "Bitmap", "Ellipse", "Polygon", "Text"):
            value = _text_by_local_name(metadata_root, key)
            if value is not None:
                try:
                    object_stats[key.lower()] = int(value)
                except ValueError:
                    object_stats[key.lower()] = value

        return {
            "filename": filename,
            "sha256": digest,
            "container_type": mimetype,
            "product_name": _text_by_local_name(metadata_root, "ProductName"),
            "app_version": _text_by_local_name(metadata_root, "AppVersion"),
            "build_number": _text_by_local_name(metadata_root, "BuildNumber"),
            "page_count": int(_text_by_local_name(metadata_root, "NumPages") or 0),
            "layer_count": int(_text_by_local_name(metadata_root, "NumLayers") or 0),
            "page_dimensions": _text_by_local_name(metadata_root, "PageDimensions"),
            "layer_names": layer_names,
            "object_names": object_names,
            "object_stats": object_stats,
            "text_runs": _all_text_by_local_name(text_root, "TextRun"),
            "preview_data_url": (
                "data:image/png;base64," + base64.b64encode(preview).decode("ascii")
                if preview else None
            ),
        }
