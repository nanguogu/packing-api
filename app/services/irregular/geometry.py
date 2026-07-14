"""Normalize SVG production outlines into centimetre polygons."""

from __future__ import annotations

import io
import math
from typing import Iterable

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid
from svgelements import Path, SVG, Shape


PX_PER_CM = 96.0 / 2.54


def _polygons(geometry) -> Iterable[Polygon]:
    if isinstance(geometry, Polygon):
        yield geometry
    elif isinstance(geometry, (MultiPolygon, GeometryCollection)):
        for item in geometry.geoms:
            yield from _polygons(item)


def _sample_subpath(subpath: Path, tolerance_cm: float) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    tolerance_px = max(tolerance_cm * PX_PER_CM, 0.05)
    for segment in subpath:
        name = type(segment).__name__.lower()
        if name == "move":
            point = segment.end
            points.append((point.x / PX_PER_CM, point.y / PX_PER_CM))
            continue
        try:
            length = float(segment.length())
        except (AttributeError, TypeError, ValueError):
            length = tolerance_px
        steps = max(1, min(1000, math.ceil(length / tolerance_px)))
        for index in range(1, steps + 1):
            point = segment.point(index / steps)
            candidate = (point.x / PX_PER_CM, point.y / PX_PER_CM)
            if not points or candidate != points[-1]:
                points.append(candidate)
    if len(points) >= 3 and points[0] != points[-1]:
        points.append(points[0])
    return points


def _rings_to_geometry(rings: list[list[tuple[float, float]]]):
    ring_polygons = []
    for ring in rings:
        if len(ring) < 4:
            continue
        geometry = make_valid(Polygon(ring))
        ring_polygons.extend(p for p in _polygons(geometry) if p.area > 1e-6)
    if not ring_polygons:
        return GeometryCollection()

    # Even/odd fill: a ring inside an odd number of larger rings is a hole.
    ordered = sorted(ring_polygons, key=lambda value: value.area, reverse=True)
    result = GeometryCollection()
    for index, polygon in enumerate(ordered):
        point = polygon.representative_point()
        depth = sum(1 for parent in ordered[:index] if parent.contains(point))
        result = result.difference(polygon) if depth % 2 else unary_union([result, polygon])
    return make_valid(result)


def geometry_to_polygons(geometry) -> list[dict]:
    output = []
    for polygon in _polygons(make_valid(geometry)):
        output.append({
            "outer": [(round(x, 4), round(y, 4)) for x, y in list(polygon.exterior.coords)[:-1]],
            "holes": [
                [(round(x, 4), round(y, 4)) for x, y in list(interior.coords)[:-1]]
                for interior in polygon.interiors
            ],
        })
    return output


def polygons_to_geometry(polygons) -> Polygon | MultiPolygon:
    values = [Polygon(item.outer, item.holes) for item in polygons]
    geometry = make_valid(unary_union(values))
    if geometry.is_empty:
        raise ValueError("A packing unit has empty or invalid polygon geometry")
    return geometry


def parse_svg_components(
    svg_bytes: bytes, tolerance_cm: float = 0.05, *, confirmed_only: bool = False
) -> list[dict]:
    """Parse transformed SVG shapes; ``pack-*`` ids mark confirmed outlines.

    Inspection uses ``confirmed_only`` so a legacy artwork file with hundreds
    of decorative curves does not spend minutes polygonizing objects that are
    explicitly ineligible for solving.
    """
    if not svg_bytes:
        return []
    try:
        document = SVG.parse(io.StringIO(svg_bytes.decode("utf-8-sig")))
    except Exception as exc:
        raise ValueError(f"Unable to parse SVG: {exc}") from exc

    components = []
    for index, element in enumerate(document.elements()):
        if not isinstance(element, Shape) or isinstance(element, SVG):
            continue
        element_id = getattr(element, "id", None) or f"shape-{index}"
        confirmed = str(element_id).lower().startswith("pack-")
        if confirmed_only and not confirmed:
            continue
        try:
            path = Path(element)
            rings = [_sample_subpath(subpath, tolerance_cm) for subpath in path.as_subpaths()]
            geometry = _rings_to_geometry(rings)
        except Exception as exc:
            raise ValueError(f"Unable to normalize SVG element {index}: {exc}") from exc
        if geometry.is_empty or geometry.area <= 1e-6:
            continue
        min_x, min_y, max_x, max_y = geometry.bounds
        components.append({
            "component_id": element_id,
            "confirmed_packing_outline": confirmed,
            "polygons": geometry_to_polygons(geometry),
            "bounds_cm": {
                "x": round(min_x, 4), "y": round(min_y, 4),
                "width": round(max_x - min_x, 4), "height": round(max_y - min_y, 4),
            },
            "area_cm2": round(geometry.area, 4),
        })
    return components
