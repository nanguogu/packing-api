"""Small stdin/stdout process wrapper around U-Nesting's documented C FFI.

Run with ``python -m app.services.irregular.u_nesting_ffi`` and configure
``U_NESTING_LIBRARY`` to the built DLL/shared-library path. Keeping this in a
child process lets the API enforce a hard timeout around native code.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys


def main() -> int:
    library_path = os.getenv("U_NESTING_LIBRARY")
    if not library_path:
        raise RuntimeError("U_NESTING_LIBRARY is not configured")
    library = ctypes.CDLL(library_path)
    library.unesting_solve.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    library.unesting_solve.restype = ctypes.c_int
    library.unesting_free_string.argtypes = [ctypes.c_void_p]
    request = sys.stdin.buffer.read()
    output = ctypes.c_void_p()
    status = library.unesting_solve(request, ctypes.byref(output))
    if not output.value:
        raise RuntimeError(f"U-Nesting returned status {status} without a response")
    try:
        response = ctypes.string_at(output.value)
        parsed = json.loads(response)
        sys.stdout.write(json.dumps(parsed, ensure_ascii=False))
    finally:
        library.unesting_free_string(output)
    return 0 if status == 0 else status


if __name__ == "__main__":
    raise SystemExit(main())
