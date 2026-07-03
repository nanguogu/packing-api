# Packing Optimization & Logistics Recommendation API

## Project Overview

MVP for automated bin-packing optimization and shipping rate comparison.
Products have diverse shapes/weights → AI automates box sizing to improve space utilization and reduce shipping costs.

## Key Technical Decisions

### Engine (OR-Tools CP-SAT)
- Virtual box strategy: minimize maxX + maxY + maxZ
- Level 1 approved path: exact minimum-volume objective for one custom carton containing exactly 3 cuboids
- 6 rotation orientations, N<=5 <500ms solve time
- Constraints: no overlap, items within bounds

### Grouping (Priority System)
- P0: Shipping limits (weight split, dual-path)
- P1: must_pack / must_not_pack (hard rules, Union-Find merge)
- P2: pack_near (soft preference)
- must_pack overrides must_not when conflicting

### Shipping Pricing (3 Carriers)
- Production-facing public quote API: `POST /shipping/quote`
- Origin scope: Hong Kong export; currency: HKD
- Current verified lane: Hong Kong → Singapore, Priority
- Multi-piece shipment: calculate each carton's billable weight, then rate the aggregated shipment weight
- Versioned public rate card: `app/config/public_rates_hk_2026.json`
- Unsupported lanes/services/weights return 422 instead of falling back to an invented zone

### Legacy Packing Estimate
- DHL: Oversize $30 + Overweight $100 (CHAIN stacking) + fuel 36%
- UPS: AHS $46 OR LPS $219 (LPS REPLACES AHS) + fuel 46%
- FedEx: AHS $46 OR Oversize $255 (Oversize REPLACES AHS) + fuel 46%
- Dim weight: metric ÷5000, imperial ÷139, 2025 round-up rule
- Recommendation: cheapest total cost across all 3

### Dual-Path
- Path A (compliant, no surcharges) vs Path B (optimal + surcharges)
- Feasibility: explicit sorted dimension comparison +1cm tolerance
- If Path A infeasible → path_b_only

### Visualization
- Plotly.js Mesh3d + wireframe, standalone HTML
- .replace() template injection (NOT f-string)
- Step-through animation + keyboard shortcuts

## Critical Fixes to Remember

1. py3dbp: use float(i.weight), NOT int() — ZeroDivisionError
2. Grouper: P0 weight split must run even when rules=[]
3. Path A feasibility: sorted dimension comparison, NOT engine flags
4. f-string: CSS {} needs {{}} or use .replace() template
5. Windows: GBK codec fails on emojis, use ASCII

## Status

- 193 tests all passing
- Level 1 complete: `POST /pack/level1` performs 3-item minimum-volume packing and HK→SG Priority carrier comparison
- Level 1 guide: `POST /pack/level1/viz` returns an item-by-item interactive 3D work instruction
- Level 1 layouts enforce floor placement or full single-item support; minimum volume ties prefer smaller edge sum and compact coordinates
- D1-D14 complete, D7 (PDF) and D15 (deploy) pending
- Public logistics quote engine integrated; Singapore Priority full weight bands available
- Public quote includes configured physical handling surcharges; remote-area charges, duties, taxes, and input-dependent packaging fees remain excluded
- Level 2/3 packing requirements remain pending; legacy minimum-envelope behavior is not the approved cost optimizer
- Legacy packing estimator base rates remain hardcoded; surcharge rules are JSON-configurable
- PostgreSQL required for DB-backed endpoints

## Commands

```bash
# Tests
.venv/Scripts/python.exe -m pytest tests/ -v

# API (requires PostgreSQL)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
