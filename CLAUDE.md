# Packing Optimization & Logistics Recommendation API

## Project Overview

MVP for automated bin-packing optimization and shipping rate comparison.
Company products have diverse shapes/weights → manual packing is inefficient → AI automates box sizing to improve space utilization and reduce shipping costs.

## Architecture

```
packing-api/
├── app/
│   ├── main.py              # FastAPI entry point (uvicorn)
│   ├── database.py          # SQLAlchemy + PostgreSQL
│   ├── models.py            # ORM models (Product, GroupRule)
│   ├── schemas.py           # Pydantic request/response schemas
│   ├── routers/
│   │   ├── pack.py          # /pack/* endpoints (5 endpoints)
│   │   ├── products.py      # /products CRUD endpoints
│   │   └── shipping.py      # /shipping/quote public-rate comparison
│   ├── services/
│   │   ├── packer.py        # Full packing pipeline orchestration
│   │   ├── grouper.py       # Grouping constraints (Union-Find)
│   │   ├── shipping.py      # Legacy packing estimate (USD demo tables)
│   │   ├── public_shipping.py # Versioned HKD public-rate quote engine
│   │   ├── packing_list.py  # Packing list generation
│   │   └── viz.py           # 3D visualization HTML generator
│   └── core/
│       ├── engine.py        # OR-Tools CP-SAT solver (virtual box strategy)
│       ├── verifier.py      # py3dbp greedy verification + cross-check
│       └── config.py        # Config from env vars + box presets
├── 3D-bin-packing/          # py3dbp library (patched - see below)
├── boxing-match/            # Reference project (boxviz.js)
├── data/                    # Sample products CSV
├── scripts/                 # import_products.py
├── tests/                   # 171 tests, all passing
├── requirements.txt
├── .env                     # DATABASE_URL + REDIS_URL
└── .gitignore
```

## API Endpoints

| Endpoint | Method | Description | Output |
|---|---|---|---|
| `/pack/direct` | POST | Pack items directly (no DB) | JSON |
| `/pack/list` | POST | Structured packing list | JSON |
| `/pack/list/text` | POST | Human-readable packing list | JSON |
| `/pack/viz` | POST | Interactive 3D visualization | HTML |
| `/pack/` | POST | Pack by SKUs (requires DB) | JSON |
| `/pack/detail` | POST | Detailed pack by SKUs (requires DB) | JSON |
| `/pack/level1` | POST | Exactly 3 cuboids → minimum-volume custom carton → public carrier quote | JSON |
| `/pack/level1/viz` | POST | Level 1 item-by-item interactive 3D packing guide | HTML |
| `/shipping/quote` | POST | Multi-carton public-rate comparison | JSON |

## Key Technical Decisions

### Engine (OR-Tools CP-SAT)
- "Virtual box" strategy: minimize maxX + maxY + maxZ (not predefined box sizes)
- Items can rotate (6 orientations: LWH, LHW, WLH, WHL, HLW, HWL)
- Constraints: no overlap, items within virtual bounds
- N<=5 solves in <500ms, N=3 in <50ms

### Grouping Constraints (Priority System)
- **P0 (highest)**: Shipping limits (max weight → weight split, max dims → dual-path)
- **P1**: must_pack_together / must_not_pack_together (hard rules)
- **P2 (lowest)**: pack_near (soft preference, no conflict)
- Union-Find data structure for efficient group merging
- must_pack overrides must_not when they conflict (P1 logic)

### Shipping Pricing
- **3 carriers**: DHL, UPS, FedEx
- **Dim weight**: metric L×W×H÷5000, imperial ÷139
- **2025 rule**: dimensions rounded UP before volume calculation
- **Surcharges**:
  - DHL: Oversize $30 + Overweight $100 (CHAIN stacking) + fuel ~36%
  - UPS: AHS $46 OR LPS $219 (LPS REPLACES AHS, not stacked) + fuel ~46%
  - FedEx: AHS $46 OR Oversize $255 (Oversize REPLACES AHS) + fuel ~46%
- **2026 cubic triggers**: 10,368in³→AHS, 17,280in³→Oversize
- **Recommendation**: compare all 3 → pick cheapest total cost

### Dual-Path Comparison
- **Path A**: compliant box within shipping limits (no surcharges)
- **Path B**: optimal minimum box + surcharges
- Compare total shipping cost → recommend cheapest
- If Path A infeasible (items can't fit within limits) → path_b_only
- Feasibility check: explicit sorted dimension comparison against sorted limits (+1cm tolerance)

### 3D Visualization
- Plotly.js Mesh3d for item cuboids + Scatter3d wireframe for container
- Standalone HTML page (no external dependencies beyond Plotly.js CDN)
- Step-through animation: Forward/Back/Reset/Show All
- Keyboard shortcuts: Arrow keys, R=reset, A=show all
- Uses `.replace()` template injection (NOT f-string, to avoid CSS/JS {} escaping)

### Packing List
- Structured dict: metadata + boxes + shipping_summary
- Text format: human-readable for print/email
- Each box: dimensions, items with positions/rotations, weight, utilization, dual-path info

## Known Fixes / Lessons Learned

1. **py3dbp ZeroDivisionError**: `int(i.weight)` converted 0.5→0, causing gravityCenter division by zero. Fixed: use `float(i.weight)` + zero-total guard.
2. **Grouper P0 weight split not triggering**: When `rules=[]`, early return `[items]` happened before P0 check. Fixed: restructured flow to always check P0 limits.
3. **Dim weight test value mismatch**: 2025 round-up rule changes expected values. Verify rounding logic carefully.
4. **Path A feasibility (CRITICAL)**: Engine `exceeds_limits` flag doesn't mean constrained result exceeds limits. Use **explicit sorted dimension comparison** instead of relying on engine flags.
5. **f-string CSS escaping**: CSS `{margin: 0}` inside f-string gets parsed as Python expression. Use `.replace()` template injection or `{{}}` doubling.
6. **Unicode encoding on Windows**: GBK codec can't encode emojis. Use ASCII "PASS"/"FAIL" in benchmark scripts.

## Project Status

### Public logistics quotation
- Integrated on `master` via commits `74a838a` and `54d76db`
- Hong Kong export, HKD, Hong Kong → Singapore Priority
- DHL / UPS / FedEx multi-piece shipment comparison
- Full published Singapore Priority weight bands, including high-weight per-kg rates
- Unknown lanes and unsupported weights fail explicitly with HTTP 422
- Configured physical handling surcharges are included; remote-area charges, duties, taxes, and input-dependent packaging fees remain pending

### Packing product direction
- Level 1 is approved and implemented: one order, exactly three freely rotatable cuboids, one minimum-volume custom carton, followed by HK→SG Priority public-rate comparison
- Level 1 requires each item to sit on the floor or be fully supported; equal-volume cartons use edge-sum and coordinate compactness as tie-breakers
- Level 2 will add rearrangement/split-carton alternatives driven by oversize and overweight cost
- Level 3 will add heavy/fragile/must-pack constraints and compare unrestricted items across constrained groups
- The legacy edge-sum objective remains available but is not the approved Level 1 optimizer

### Completed (W1 + W2 + W3)
- D1: Engine (OR-Tools CP-SAT) - 20 tests
- D2: Verifier (py3dbp cross-check) - 16 tests
- D3: Grouper (Union-Find + P0/P1/P2) - 21 tests
- D4: Packer pipeline (orchestration)
- D5: CSV import + benchmarks
- D6: Shipping pricing (3 carriers + surcharges) - 38 tests
- D8: Packing-shipping integration
- D9: Dual-path comparison - 13 tests
- D10: E2E validation - 17 tests (125/125 all passing)
- D11-D12: 3D visualization (Plotly.js)
- D13: Packing list generation
- D14: Integration validation - 23 tests
- **Total: 193 tests, all passing**

### Pending
- D7: PDF parsing for shipping rate data (base rates currently hardcoded)
- D15: Deployment (requires PostgreSQL setup)
- Complete public-rate lanes/services beyond Singapore Priority
- Public special-handling, remote-area, duties, and tax rules
- Replace public rates with client negotiated rates

## Running Tests

```bash
cd packing-api
.venv/Scripts/python.exe -m pytest tests/ -v
```

## Running the API

```bash
# Requires PostgreSQL running with packing_db created
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Swagger docs: http://localhost:8000/docs
```

## Environment Setup

```bash
# Python 3.14 (venv at .venv/)
pip install -r requirements.txt

# PostgreSQL: create database and user
# DATABASE_URL in .env: postgresql://packing:packing123@localhost:5432/packing_db
```

## Constraints for Further Development

- **3-week MVP scope**: no "顺便" features, strict deadline
- **N<=5 priority**: solver must be fast for small item counts
- **No STEP file parsing**: MVP reads dimensions from Excel/ERP, not CAD files
- **Single recommended solution**: output 1 best plan, not multi-plan comparison
- **Hardcoded surcharges**: DHL/UPS/FedEx rules in if-else, refactoring later
