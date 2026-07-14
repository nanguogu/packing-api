"""Windows-only CorelDRAW COM conversion worker."""

from __future__ import annotations

import hashlib
import platform
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response


app = FastAPI(title="CorelDRAW CDR to SVG worker")


@app.get("/health")
def health():
    return {"status": "ok", "platform": platform.system()}


@app.post("/convert")
async def convert(cdr_file: UploadFile = File(...)):
    if platform.system() != "Windows":
        raise HTTPException(status_code=503, detail="CorelDRAW conversion requires Windows")
    content = await cdr_file.read()
    if not content:
        raise HTTPException(status_code=422, detail="CDR file is empty")
    try:
        import pythoncom
        from win32com.client import constants, gencache
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="pywin32 is not installed") from exc

    with tempfile.TemporaryDirectory(prefix="cdr-convert-") as directory:
        source = Path(directory) / "source.cdr"
        target = Path(directory) / "normalized.svg"
        source.write_bytes(content)
        document = None
        application = None
        pythoncom.CoInitialize()
        try:
            application = gencache.EnsureDispatch("CorelDRAW.Application")
            application.Visible = False
            document = application.OpenDocument(str(source))
            # CorelDRAW 2024 declares both optional option arguments as COM
            # interface types.  pywin32 cannot marshal its missing-argument
            # sentinel for those parameters, so create the official option
            # objects explicitly and use ExportEx/Finish.
            export_options = application.CreateStructExportOptions()
            palette_options = application.CreateStructPaletteOptions()
            export_options.Overwrite = True
            export_filter = document.ExportEx(
                str(target), constants.cdrSVG, constants.cdrCurrentPage,
                export_options, palette_options,
            )
            export_filter.Finish()
            if not target.exists():
                raise RuntimeError("CorelDRAW did not create the SVG output")
            return Response(
                target.read_bytes(), media_type="image/svg+xml",
                headers={"X-Source-SHA256": hashlib.sha256(content).hexdigest()},
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"CorelDRAW export failed: {exc}") from exc
        finally:
            if document is not None:
                document.Close()
            if application is not None:
                application.Quit()
            pythoncom.CoUninitialize()
