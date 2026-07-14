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
