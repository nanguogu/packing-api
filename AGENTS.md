# Packing Optimization & Logistics Recommendation API

## Project Overview

MVP for automated bin-packing optimization and shipping rate comparison.
Products have diverse shapes/weights → AI automates box sizing to improve space utilization and reduce shipping costs.

## Key Technical Decisions

### Engine (OR-Tools CP-SAT)
- Virtual box strategy: minimize maxX + maxY + maxZ
- 6 rotation orientations, N<=5 <500ms solve time
- Constraints: no overlap, items within bounds

### Grouping (Priority System)
- P0: Shipping limits (weight split, dual-path)
- P1: must_pack / must_not_pack (hard rules, Union-Find merge)
- P2: pack_near (soft preference)
- must_pack overrides must_not when conflicting

### Shipping Pricing (3 Carriers)
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

- 171 tests all passing
- D1-D14 complete, D7 (PDF) and D15 (deploy) pending
- Base rates hardcoded, surcharges in if-else (refactor later)
- PostgreSQL required for DB-backed endpoints

## Commands

```bash
# Tests
.venv/Scripts/python.exe -m pytest tests/ -v

# API (requires PostgreSQL)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
