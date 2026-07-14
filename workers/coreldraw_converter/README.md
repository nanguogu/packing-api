# CorelDRAW conversion worker

Run this worker only on a licensed Windows machine with CorelDRAW installed.
It opens a CDR through CorelDRAW COM automation and returns an SVG from
`POST /convert`.

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app:app --host 0.0.0.0 --port 8091
```

On the packing API machine set:

```powershell
$env:CDR_CONVERTER_URL='http://converter-host:8091'
```

Restrict the worker to the internal network and one conversion at a time. The
API also accepts a manually exported SVG when this worker is unavailable.
