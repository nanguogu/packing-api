param(
    [string]$SourceDir = "C:\Users\Public\packing-deps\source-archive\u-nesting-main",
    [string]$RustHome = "C:\Users\Public\rust-gnu-real",
    [string]$MinGwHome = "C:\Users\Public\winlibs-real",
    [string]$TargetDir = "C:\Users\Public\packing-build\u-nesting-winlibs"
)

$ErrorActionPreference = "Stop"

$SourceDir = [System.IO.Path]::GetFullPath($SourceDir)
$RustHome = [System.IO.Path]::GetFullPath($RustHome)
$MinGwHome = [System.IO.Path]::GetFullPath($MinGwHome)
$TargetDir = [System.IO.Path]::GetFullPath($TargetDir)

foreach ($Path in @($SourceDir, $RustHome, $MinGwHome, $TargetDir)) {
    if ($Path -match '[^\x00-\x7F]') {
        throw "U-Nesting's GNU linker paths must contain ASCII characters only: $Path"
    }
}

$Cargo = Join-Path $RustHome "bin\cargo.exe"
$Rustc = Join-Path $RustHome "bin\rustc.exe"
$Gcc = Join-Path $MinGwHome "bin\gcc.exe"
$Manifest = Join-Path $SourceDir "Cargo.toml"
$RuntimeLibraries = Join-Path $RustHome "lib\rustlib\x86_64-pc-windows-gnu\lib\self-contained"

foreach ($RequiredPath in @($Cargo, $Rustc, $Gcc, $Manifest, $RuntimeLibraries)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required U-Nesting build input was not found: $RequiredPath"
    }
}

$env:PATH = "$(Join-Path $MinGwHome 'bin');$(Join-Path $RustHome 'bin');$env:PATH"
$env:LIBRARY_PATH = $RuntimeLibraries
$env:RUSTC = $Rustc
$env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = $Gcc
$env:CARGO_TARGET_DIR = $TargetDir

& $Cargo build --manifest-path $Manifest -p u-nesting-ffi --release
if ($LASTEXITCODE -ne 0) {
    throw "U-Nesting build failed with exit code $LASTEXITCODE"
}

$Library = Join-Path $TargetDir "release\u_nesting_ffi.dll"
if (-not (Test-Path -LiteralPath $Library)) {
    throw "U-Nesting build completed without the expected DLL: $Library"
}

Write-Output $Library
