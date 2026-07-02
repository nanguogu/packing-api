"""3D packing visualization renderer.

Generates interactive Plotly.js HTML visualization from packer results,
reusing boxing-match's cuboid mesh + boxviz.js animation approach.

Adapted from:
  - boxing-match/cartonise.py (_cuboid_mesh, add_cuboid_mesh, render_html)
  - boxing-match/boxviz.js (step-through animation)

Output: standalone HTML page with embedded Plotly.js + interactive controls.
Items are rendered as Mesh3d cuboids, container as wireframe edges.
"""

from __future__ import annotations

import json
import os


# ---------------------------------------------------------------------------
# Cuboid mesh generation (from boxing-match _cuboid_mesh)
# ---------------------------------------------------------------------------

def _cuboid_mesh(x: float, y: float, z: float, sx: float, sy: float, sz: float):
    """Generate vertices and triangle indices for a 3D cuboid.

    Args:
        x, y, z: Origin position (cm).
        sx, sy, sz: Dimensions along each axis (cm).

    Returns:
        Dict with keys: vx, vy, vz (8 vertices), i, j, k (12 triangles).
    """
    # 8 vertices of the cuboid
    vx = [x,      x + sx, x,      x + sx, x,      x + sx, x,      x + sx]
    vy = [y,      y,      y + sy, y + sy, y,      y,      y + sy, y + sy]
    vz = [z,      z,      z,      z + sz, z + sz, z + sz, z + sz, z + sz]

    # 12 triangles (2 per face, 6 faces)
    i_arr = [0, 0, 4, 4, 0, 0, 2, 2, 0, 0, 2, 2]
    j_arr = [1, 3, 5, 7, 2, 6, 3, 7, 1, 5, 1, 3]
    k_arr = [3, 1, 7, 5, 6, 2, 7, 3, 5, 1, 3, 7]

    return {"vx": vx, "vy": vy, "vz": vz, "i": i_arr, "j": j_arr, "k": k_arr}


# ---------------------------------------------------------------------------
# Color generation
# ---------------------------------------------------------------------------

def _generate_colors(n: int, base_colors: list[str] | None = None) -> list[str]:
    """Generate distinct colors for n items.

    Args:
        n: Number of items.
        base_colors: Optional list of hex colors. If None, use default palette.

    Returns:
        List of hex color strings.
    """
    # Default palette — visually distinct colors
    palette = [
        "#ff8c00", "#1f77b4", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
        "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
    ]

    if base_colors:
        # Use custom colors first, then fill from palette/HSV
        colors = list(base_colors)
        for i in range(len(base_colors), n):
            if i < len(palette):
                colors.append(palette[i])
            else:
                hue = (i * 360 / n) % 360
                sat = 0.7
                val = 0.85
                c = val * sat
                xv = c * (1 - abs((hue / 60) % 2 - 1))
                m = val - c
                if hue < 60:
                    r, g, b = c, xv, 0
                elif hue < 120:
                    r, g, b = xv, c, 0
                elif hue < 180:
                    r, g, b = 0, c, xv
                elif hue < 240:
                    r, g, b = 0, xv, c
                elif hue < 300:
                    r, g, b = xv, 0, c
                else:
                    r, g, b = c, 0, xv
                r, g, b = int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)
                colors.append(f"#{r:02x}{g:02x}{b:02x}")
        return colors

    if n <= len(palette):
        return palette[:n]

    # Generate more colors using HSV rotation
    colors = list(palette)
    for i in range(len(palette), n):
        hue = (i * 360 / n) % 360
        sat = 0.7
        val = 0.85
        c = val * sat
        xv = c * (1 - abs((hue / 60) % 2 - 1))
        m = val - c
        if hue < 60:
            r, g, b = c, xv, 0
        elif hue < 120:
            r, g, b = xv, c, 0
        elif hue < 180:
            r, g, b = 0, c, xv
        elif hue < 240:
            r, g, b = 0, xv, c
        elif hue < 300:
            r, g, b = xv, 0, c
        else:
            r, g, b = c, 0, xv
        r, g, b = int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)
        colors.append(f"#{r:02x}{g:02x}{b:02x}")

    return colors


# ---------------------------------------------------------------------------
# HTML template (plain string, no f-string — avoids CSS/JS {} escaping issues)
# ---------------------------------------------------------------------------

BOYZVIZ_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),  # app -> packing-api
    "boxing-match", "boxviz.js"
)

_VIZ_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Packing 3D Visualization</title>
    <script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
    <style>
        body { margin: 0; font-family: system-ui, sans-serif; }
        #viz { width: 100%; height: 85vh; }
        #controls {
            padding: 10px 20px;
            background: #f5f5f5;
            display: flex;
            gap: 8px;
            align-items: center;
        }
        #controls button { padding: 6px 12px; border: 1px solid #ccc; cursor: pointer; }
        #controls button:hover { background: #ddd; }
        #info { padding: 8px 20px; font-size: 14px; color: #666; }
    </style>
</head>
<body>
    <div id="controls">
        <button onclick="stepBack()">Back</button>
        <button onclick="stepForward()">Forward</button>
        <button onclick="resetView()">Reset View</button>
        <button onclick="toggleAll()">Show All</button>
    </div>
    <div id="info"></div>
    <div id="viz"></div>

    <script>
    // Packing data
    window.BOXVIZ_DATA = __BOXVIZ_DATA__;

    var traces = __TRACES_JSON__;
    var currentStep = 0;
    var steps = window.BOXVIZ_DATA.stepGroups;

    // Layout configuration
    var layout = {
        scene: {
            xaxis: {title: 'Length (cm)', showgrid: true},
            yaxis: {title: 'Width (cm)', showgrid: true},
            zaxis: {title: 'Height (cm)', showgrid: true},
            camera: {
                eye: {x: window.BOXVIZ_DATA.fitEye.x,
                       y: window.BOXVIZ_DATA.fitEye.y,
                       z: window.BOXVIZ_DATA.fitEye.z}
            },
            aspectratio: {
                x: window.BOXVIZ_DATA.aspect.x,
                y: window.BOXVIZ_DATA.aspect.y,
                z: window.BOXVIZ_DATA.aspect.z
            }
        },
        margin: {l: 0, r: 0, t: 30, b: 0},
        title: 'Packing Visualization'
    };

    // Initially hide all item traces (wireframe always visible)
    for (var i = 0; i < traces.length; i++) {
        if (traces[i].type === 'mesh3d') {
            traces[i].visible = false;
        }
    }

    Plotly.newPlot('viz', traces, layout);

    function stepForward() {
        if (currentStep < steps.length) {
            var indices = steps[currentStep];
            for (var idx of indices) {
                Plotly.restyle('viz', {visible: true}, [idx]);
            }
            currentStep++;
            updateInfo();
        }
    }

    function stepBack() {
        if (currentStep > 0) {
            currentStep--;
            var indices = steps[currentStep];
            for (var idx of indices) {
                Plotly.restyle('viz', {visible: false}, [idx]);
            }
            updateInfo();
        }
    }

    function resetView() {
        currentStep = 0;
        for (var i = 0; i < traces.length; i++) {
            if (traces[i].type === 'mesh3d') {
                Plotly.restyle('viz', {visible: false}, [i]);
            }
        }
        Plotly.relayout('viz', {
            'scene.camera.eye': {x: window.BOXVIZ_DATA.fitEye.x,
                                   y: window.BOXVIZ_DATA.fitEye.y,
                                   z: window.BOXVIZ_DATA.fitEye.z}
        });
        updateInfo();
    }

    function toggleAll() {
        currentStep = steps.length;
        for (var i = 0; i < traces.length; i++) {
            if (traces[i].type === 'mesh3d') {
                Plotly.restyle('viz', {visible: true}, [i]);
            }
        }
        updateInfo();
    }

    function updateInfo() {
        var totalItems = window.BOXVIZ_DATA.totals.item || 0;
        var shown = 0;
        for (var s = 0; s < currentStep; s++) {
            shown += steps[s].length;
        }
        document.getElementById('info').textContent =
            'Step ' + currentStep + '/' + steps.length +
            ' | Items shown: ' + shown + '/' + totalItems;
    }

    // Keyboard shortcuts
    document.addEventListener('keydown', function(e) {
        if (e.key === 'ArrowRight') stepForward();
        if (e.key === 'ArrowLeft') stepBack();
        if (e.key === 'r' || e.key === 'R') resetView();
        if (e.key === 'a' || e.key === 'A') toggleAll();
    });

    updateInfo();
    </script>
</body>
</html>"""


def generate_3d_html(packer_result: dict) -> str:
    """Generate a standalone HTML page with interactive 3D packing visualization.

    Uses Plotly.js Mesh3d for cuboid rendering and optional boxviz.js
    for step-through animation.

    Args:
        packer_result: Output from pack_items() service.

    Returns:
        Complete HTML string (standalone, no external dependencies beyond
        Plotly.js CDN).
    """
    groups = packer_result.get("groups", [])
    if not groups:
        return _generate_empty_html()

    # Build Plotly traces for each group/box
    all_traces = []
    all_step_groups = []
    boxviz_data = {
        "stepGroups": [],
        "groupKinds": [],
        "groupClasses": [],
        "rigidAll": [],
        "totals": {},
        "uniqColorList": [],
        "fitEye": {"x": 0, "y": -2.5, "z": 1.5},
        "aspect": {"x": 1, "y": 1, "z": 1},
        "hasRubble": False,
    }

    trace_idx = 0
    total_items = 0

    for group_idx, group in enumerate(groups):
        if not group.get("box") or not group.get("layout"):
            continue

        box = group["box"]
        layout = group["layout"]

        # Container wireframe
        L, W, H = box["length_cm"], box["width_cm"], box["height_cm"]

        # Wireframe edges
        edges = [
            # Bottom face
            [(0, 0, 0), (L, 0, 0)],
            [(L, 0, 0), (L, W, 0)],
            [(L, W, 0), (0, W, 0)],
            [(0, W, 0), (0, 0, 0)],
            # Top face
            [(0, 0, H), (L, 0, H)],
            [(L, 0, H), (L, W, H)],
            [(L, W, H), (0, W, H)],
            [(0, W, H), (0, 0, H)],
            # Vertical edges
            [(0, 0, 0), (0, 0, H)],
            [(L, 0, 0), (L, 0, H)],
            [(L, W, 0), (L, W, H)],
            [(0, W, 0), (0, W, H)],
        ]

        for edge in edges:
            p1, p2 = edge
            all_traces.append({
                "type": "scatter3d",
                "mode": "lines",
                "x": [p1[0], p2[0]],
                "y": [p1[1], p2[1]],
                "z": [p1[2], p2[2]],
                "line": {"width": 2, "color": "#555"},
                "hoverinfo": "skip",
                "showlegend": False,
            })

        # Item cuboids
        colors = _generate_colors(len(layout))
        step_items = []

        for item_idx, entry in enumerate(layout):
            pos = entry.get("position") or {}
            pdims = entry.get("placed_dims") or {}

            mesh = _cuboid_mesh(
                pos.get("x", 0), pos.get("y", 0), pos.get("z", 0),
                pdims.get("length", 0), pdims.get("width", 0), pdims.get("height", 0),
            )

            hover_text = (
                f"{entry['sku']}<br>"
                f"Position: ({pos.get('x',0):.1f}, {pos.get('y',0):.1f}, {pos.get('z',0):.1f})<br>"
                f"Dims: {pdims.get('length',0):.1f}x{pdims.get('width',0):.1f}x{pdims.get('height',0):.1f} cm<br>"
                f"Rotation: {entry.get('rotation', '?')}<br>"
                f"Weight: {entry.get('weight_kg', 0)} kg"
            )

            all_traces.append({
                "type": "mesh3d",
                "x": mesh["vx"],
                "y": mesh["vy"],
                "z": mesh["vz"],
                "i": mesh["i"],
                "j": mesh["j"],
                "k": mesh["k"],
                "color": colors[item_idx],
                "opacity": 0.85,
                "flatshading": False,
                "lighting": {"ambient": 1.0, "diffuse": 0.2, "specular": 0.0},
                "hovertext": hover_text,
                "hoverinfo": "text",
                "name": entry["sku"],
                "showlegend": True,
            })

            step_items.append(trace_idx)
            boxviz_data["uniqColorList"].append(colors[item_idx])
            boxviz_data["groupKinds"].append("rigid")
            boxviz_data["groupClasses"].append("item")
            boxviz_data["rigidAll"].append(trace_idx)
            total_items += 1
            trace_idx += 1

        trace_idx += len(edges)  # account for wireframe traces

        all_step_groups.append(step_items)

    # Build BOXVIZ_DATA
    boxviz_data["stepGroups"] = all_step_groups
    boxviz_data["totals"] = {"item": total_items}
    max_dim = max(
        max(g["box"]["length_cm"], g["box"]["width_cm"], g["box"]["height_cm"])
        for g in groups if g.get("box")
    ) if groups else 50
    boxviz_data["aspect"] = {
        "x": round(max(g["box"]["length_cm"] / max_dim for g in groups if g.get("box")), 2),
        "y": round(max(g["box"]["width_cm"] / max_dim for g in groups if g.get("box")), 2),
        "z": round(max(g["box"]["height_cm"] / max_dim for g in groups if g.get("box")), 2),
    }

    # Load boxviz.js (optional)
    boxviz_js = ""
    if os.path.exists(BOYZVIZ_JS_PATH):
        with open(BOYZVIZ_JS_PATH, "r", encoding="utf-8") as f:
            boxviz_js = f.read()

    # Inject dynamic data into HTML template via .replace()
    # This avoids f-string CSS/JS {} escaping problems entirely
    traces_json = json.dumps(all_traces)
    boxviz_json = json.dumps(boxviz_data)

    html_content = _VIZ_HTML_TEMPLATE.replace("__TRACES_JSON__", traces_json)
    html_content = html_content.replace("__BOXVIZ_DATA__", boxviz_json)

    return html_content


def _generate_empty_html() -> str:
    """Generate HTML for empty packing result."""
    return """<!DOCTYPE html>
<html><head><title>Empty Packing</title></head>
<body><h2>No items to visualize</h2></body></html>"""
