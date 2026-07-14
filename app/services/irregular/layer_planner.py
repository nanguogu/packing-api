"""Generate safe single- or multi-layer irregular packing candidates."""

from __future__ import annotations

from .nesting_engine import nest_layer


def plan_layers(items, config) -> list[dict]:
    max_layers = config.max_layers if all(item["stackable"] for item in items) else 1
    plans = []
    for layer_count in range(1, min(max_layers, len(items)) + 1):
        buckets = [[] for _ in range(layer_count)]
        areas = [0.0] * layer_count
        for item in sorted(items, key=lambda value: value["area"], reverse=True):
            target = min(range(layer_count), key=lambda index: areas[index])
            buckets[target].append(item)
            areas[target] += item["area"]
        layers = []
        try:
            for index, bucket in enumerate(buckets, start=1):
                result = nest_layer(bucket, config)
                result["layer"] = index
                layers.append(result)
        except ValueError:
            continue
        footprint_width = max(layer["used_width_cm"] for layer in layers) + 2 * config.edge_margin_cm
        footprint_height = max(layer["used_height_cm"] for layer in layers) + 2 * config.edge_margin_cm
        inner_depth = (
            sum(max(item["thickness_cm"] for item in bucket) for bucket in buckets)
            + config.top_padding_cm + config.bottom_padding_cm
            + config.interlayer_padding_cm * (layer_count - 1)
        )
        plans.append({
            "layer_count": layer_count,
            "layers": layers,
            "inner_width_cm": footprint_width,
            "inner_height_cm": footprint_height,
            "inner_depth_cm": inner_depth,
        })
    if not plans:
        raise ValueError("No feasible layer plan was found")
    return plans
