# PyInstaller spec — 단일 실행 파일(.exe / 바이너리) 빌드용.
# 사용:
#   pip install pyinstaller
#   pyinstaller packaging/collector.spec
# 산출물: dist/collector (Linux/macOS) 또는 dist/collector.exe (Windows)

# noqa: E501
block_cipher = None

a = Analysis(
    ['../collector/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'collector.cli.app',
        'collector.cli.dashboard',
        'collector.cli.review',
        'collector.cli.quota',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='collector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=True,
)
