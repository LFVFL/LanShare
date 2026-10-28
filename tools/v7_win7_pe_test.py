"""Statically inspect a PyInstaller onefile for Windows 7 PE hazards.

    python -B tools/v7_win7_pe_test.py --exe dist\\lanshare.exe --results audit.json

The archive is read in memory. Packaged DLLs and executables are never loaded.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pefile
from PyInstaller.archive.readers import CArchiveReader


FORBIDDEN_WIN7_IMPORTS = ('api-ms-win-core-path-l1-1-0.dll',)
BOOTLOADER_ALLOWED = {
    'advapi32.dll',
    'comctl32.dll',
    'gdi32.dll',
    'kernel32.dll',
    'user32.dll',
}


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def imported_dlls(data):
    pe = pefile.PE(data=data, fast_load=True)
    try:
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
            pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT'],
        ])
        names = []
        for directory in ('DIRECTORY_ENTRY_IMPORT', 'DIRECTORY_ENTRY_DELAY_IMPORT'):
            for entry in getattr(pe, directory, ()):
                name = entry.dll.decode('ascii', 'replace').lower()
                if name not in names:
                    names.append(name)
        return sorted(names)
    finally:
        pe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    exe = args.exe.resolve(strict=True)
    results = args.results.resolve()
    if results.exists():
        parser.error('Choose a new result path; existing evidence is not overwritten')

    archive = CArchiveReader(str(exe))
    members = []
    for name in sorted(archive.toc):
        if Path(name).suffix.lower() not in ('.dll', '.pyd', '.exe'):
            continue
        data = archive.extract(name)
        if not data.startswith(b'MZ'):
            continue
        imports = imported_dlls(data)
        members.append({
            'name': name,
            'size': len(data),
            'sha256': digest_bytes(data),
            'imports': imports,
        })

    bootloader_imports = imported_dlls(exe.read_bytes())
    forbidden = []
    api_set_imports = {}
    for member in members:
        for imported in member['imports']:
            if imported in FORBIDDEN_WIN7_IMPORTS:
                forbidden.append({'member': member['name'], 'dll': imported})
            if imported.startswith('api-ms-win-'):
                api_set_imports.setdefault(imported, []).append(member['name'])
    member_basenames = {Path(member['name']).name.lower() for member in members}
    required_api_sets = sorted(api_set_imports)
    missing_api_sets = [
        name for name in required_api_sets if name not in member_basenames
    ]

    report = {
        'timestamp': datetime.now(timezone.utc).astimezone().isoformat(),
        'exe': str(exe),
        'size_bytes': exe.stat().st_size,
        'sha256': digest_file(exe),
        'method': 'Read-only CArchiveReader and pefile import-table inspection; packaged PE files are not loaded',
        'archive_pe_members_scanned': len(members),
        'bootloader_imports': bootloader_imports,
        'bootloader_imports_win7_safe': set(bootloader_imports) <= BOOTLOADER_ALLOWED,
        'forbidden_win7_imports': forbidden,
        'required_api_set_libraries': required_api_sets,
        'missing_api_set_libraries': missing_api_sets,
        'ucrtbase_bundled': 'ucrtbase.dll' in member_basenames,
        'api_set_imports': dict(sorted(api_set_imports.items())),
        'members': members,
        'success': (
            not forbidden
            and set(bootloader_imports) <= BOOTLOADER_ALLOWED
            and not missing_api_sets
            and 'ucrtbase.dll' in member_basenames
        ),
        'limitations': [
            'Static imports cannot prove every dynamic LoadLibrary path or every Windows 7 patch level.',
            'The executable was not run on a Windows 7 machine as part of this audit.',
        ],
    }
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=True, indent=2)
        stream.write('\n')
    print(json.dumps({
        'success': report['success'],
        'archive_pe_members_scanned': len(members),
        'bootloader_imports': bootloader_imports,
        'forbidden_win7_imports': forbidden,
        'results': str(results),
    }, ensure_ascii=True))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
