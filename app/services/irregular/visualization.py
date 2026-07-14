"""Standalone layered SVG work instruction for irregular layouts."""

from __future__ import annotations

import html
import json


TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Irregular packing __ORDER__</title>
<style>
body{font-family:system-ui,sans-serif;background:#f4f5f7;color:#18202a;margin:0;padding:24px}main{max-width:1100px;margin:auto}
.meta,.layer{background:white;border:1px solid #dde2e7;border-radius:14px;padding:18px;margin:16px 0}.tabs button{margin:4px;padding:8px 14px}
svg{width:100%;height:auto;background:#f8fafc;border:1px solid #94a3b8}.piece{fill:#f4772e55;stroke:#b94311;stroke-width:.3}.label{font-size:3px;fill:#111}
.hidden{display:none}dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px}dt{color:#64748b}
</style></head><body><main><h1>二维装箱作业图</h1><div class="meta" id="meta"></div><div class="tabs" id="tabs"></div><div id="layers"></div></main>
<script>const DATA=__DATA__;
const dims=DATA.carton.inner_dimensions_cm;document.getElementById('meta').innerHTML=`<dl><dt>订单</dt><dd>${DATA.order_id}</dd><dt>箱体内尺寸</dt><dd>${dims.length_cm} × ${dims.width_cm} × ${dims.height_cm} cm</dd><dt>层数</dt><dd>${DATA.carton.layer_count}</dd><dt>计价线路</dt><dd>${DATA.pricing_lane_used}</dd><dt>验证</dt><dd>${DATA.verification.valid?'通过':'失败'}</dd></dl>`;
const ns='http://www.w3.org/2000/svg', root=document.getElementById('layers'), tabs=document.getElementById('tabs');
function ring(points){return points.map((p,i)=>(i?'L':'M')+(p[0]+DATA._margin)+','+(p[1]+DATA._margin)).join(' ')+' Z'}
DATA.layers.forEach((layer,index)=>{const button=document.createElement('button');button.textContent='第 '+layer.layer+' 层';tabs.appendChild(button);const panel=document.createElement('section');panel.className='layer '+(index?'hidden':'');const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${dims.length_cm} ${dims.width_cm}`);layer.placements.forEach(p=>{p.polygons.forEach(poly=>{const path=document.createElementNS(ns,'path');path.setAttribute('class','piece');path.setAttribute('fill-rule','evenodd');path.setAttribute('d',[ring(poly.outer),...poly.holes.map(ring)].join(' '));svg.appendChild(path)});const text=document.createElementNS(ns,'text');text.setAttribute('class','label');text.setAttribute('x',p.x_cm);text.setAttribute('y',p.y_cm);text.textContent=p.unit_id+' #'+p.instance+' / '+p.rotation_deg+'°';svg.appendChild(text)});panel.appendChild(svg);root.appendChild(panel);button.onclick=()=>{[...root.children].forEach(x=>x.classList.add('hidden'));panel.classList.remove('hidden')}});</script></body></html>"""


def generate_irregular_html(result: dict) -> str:
    data = {**result, "_margin": 0}
    return TEMPLATE.replace("__ORDER__", html.escape(result["order_id"])).replace(
        "__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    )
