# Irregular 2D runtime configuration

## U-Nesting

Build the U-Nesting C FFI library from the pinned and reviewed upstream source,
then configure the isolated wrapper:

```powershell
$env:U_NESTING_LIBRARY='C:\opt\u-nesting\u_nesting.dll'
$env:U_NESTING_COMMAND='C:\path\packing-api\.venv\Scripts\python.exe -m app.services.irregular.u_nesting_ffi'
```

The adapter uses the upstream documented 2D JSON contract (`geometries`,
`boundary`, `config`) and verifies the native placements independently. If the
command is not configured, the response is explicitly labelled
`python_baseline` and `fallback=true`; that fallback is for development and is
not represented as U-Nesting.

## CDR conversion

Deploy `workers/coreldraw_converter` on a licensed Windows/CorelDRAW machine and
set `CDR_CONVERTER_URL`. Alternatively, upload a manually exported SVG. Only
SVG elements whose IDs start with `pack-` become confirmed packing units.

Local CorelDRAW 2024 verification completed against `DW2606-3070.cdr`: the
worker returned a 337,585-byte SVG with a 210 mm x 297 mm page and a matching
source SHA-256. Legacy decorative paths without a `pack-*` id are skipped before
expensive polygonization; the DW inspection completes in about one second and
correctly remains `mixed / needs_review` until its packing outlines are named.

## Verified Windows development runtime

U-Nesting 0.7.1 was built as
`C:\Users\Public\packing-build\u-nesting-winlibs\release\u_nesting_ffi.dll`.
The build uses ASCII-only Rust, MinGW, source, and target paths because GNU
Binutils does not reliably resolve the current Chinese Windows profile path.
`scripts/build_u_nesting.ps1` captures the required environment. The FFI smoke
test placed two polygons with `engine.name=u-nesting`, `fallback=false`, and a
measured adapter time of about 424 ms.

For newly started API processes, configure:

```powershell
$env:U_NESTING_LIBRARY='C:\Users\Public\packing-build\u-nesting-winlibs\release\u_nesting_ffi.dll'
$env:U_NESTING_COMMAND='C:/Users/宇宙/packing-api/.venv/Scripts/python.exe -m app.services.irregular.u_nesting_ffi'
$env:CDR_CONVERTER_URL='http://127.0.0.1:8091'
```

Use forward slashes in `U_NESTING_COMMAND`: the adapter tokenizes this value
with `shlex` before starting the subprocess.
