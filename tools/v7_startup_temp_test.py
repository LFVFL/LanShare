"""Verify Windows onefile startup and extraction cleanup in owned profiles."""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from test_support import isolate_test_environment
from windows_job import OwnedProcessJob


TEST_ENV = isolate_test_environment()
ROOT = Path(__file__).resolve().parent.parent
RUNTIME_TMPDIR = r'%LOCALAPPDATA%\LanShare\Temp'

from PyInstaller.archive.readers import CArchiveReader  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')


def verify_archive(exe):
    options = CArchiveReader(str(exe)).options
    runtime_options = [option.replace('\\', '/') for option in options
                       if option.startswith('pyi-runtime-tmpdir ')]
    if runtime_options != ['pyi-runtime-tmpdir ' + RUNTIME_TMPDIR.replace('\\', '/')]:
        raise ValueError('Expected the literal per-user runtime directory option; refusing to launch')
    return runtime_options[0]


def unused_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def listing(port):
    connection = HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('GET', '/api/list?limit=1', headers={'Connection': 'close'})
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            raise RuntimeError('Owned startup endpoint returned HTTP %s' % response.status)
        return json.loads(body)
    finally:
        connection.close()


def port_is_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


class StartupTempTests(unittest.TestCase):
    exe = None
    evidence = {}

    @classmethod
    def setUpClass(cls):
        archive_option = verify_archive(cls.exe)
        cls.workspace = TEST_ENV.temporary_dir(prefix='startup-temp-')
        cls.portable = cls.workspace / 'portable'
        cls.portable.mkdir()
        cls.copied_exe = cls.portable / 'lanshare.exe'
        shutil.copy2(cls.exe, cls.copied_exe)
        verify_archive(cls.copied_exe)
        digest = hashlib.sha256(cls.exe.read_bytes()).hexdigest()
        if hashlib.sha256(cls.copied_exe.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Owned EXE copy differs from the requested artifact')
        cls.evidence.update(exe=str(cls.exe), copied_exe=str(cls.copied_exe), sha256=digest,
                            size_bytes=cls.exe.stat().st_size, runtime_tmpdir_option=RUNTIME_TMPDIR,
                            runtime_tmpdir_archive_option=archive_option,
                            runs=[], local_app_data_isolated=True)

    def setUp(self):
        self.case_root = TEST_ENV.temporary_dir(prefix='startup-case-')

    def exercise(self, label, local_app_data, temp_updates=None, executable=None, working_directory=None):
        run_exe = Path(executable or self.copied_exe).resolve(strict=True)
        run_cwd = Path(working_directory or self.portable).resolve(strict=True)
        self.assertTrue(run_exe.is_file())
        self.assertTrue(run_cwd.is_dir())
        self.assertEqual(hashlib.sha256(run_exe.read_bytes()).hexdigest(), self.evidence['sha256'])
        local_app_data.mkdir(parents=True, exist_ok=True)
        runtime_base = local_app_data / 'LanShare' / 'Temp'
        self.assertEqual(list(runtime_base.glob('_MEI*')), [], 'Previous extraction still exists')
        profile = self.case_root / ('profile-' + label)
        profile.mkdir()
        share = profile / 'share'
        share.mkdir()
        (share / 'owned.txt').write_text('Synthetic startup fixture.\n', encoding='ascii')
        environment = dict(os.environ)
        for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX',
                    'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QTDIR'):
            environment.pop(key, None)
        windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        environment.update(LOCALAPPDATA=str(local_app_data), QT_QPA_PLATFORM='offscreen',
                           LANSHARE_DATA_DIR=str(profile / 'chat_data'),
                           PATH=os.pathsep.join((str(windows / 'System32'), str(windows))))
        if temp_updates is not None:
            environment.update(temp_updates)
        entry = dict(case=label, local_app_data=str(local_app_data), runtime_base=str(runtime_base),
                     executed_exe=str(run_exe), working_directory=str(run_cwd),
                     original_executable_location=run_exe == self.exe,
                     temp=environment.get('TEMP'), tmp=environment.get('TMP'),
                     temporary_environment_inherited=temp_updates is None, success=False)
        self.evidence['runs'].append(entry)
        process = None
        job = OwnedProcessJob()
        port = unused_port()
        started = time.monotonic()
        try:
            self.assertEqual(sorted(path.name for path in self.portable.iterdir()), ['lanshare.exe'])
            process = subprocess.Popen(
                [str(run_exe), str(share), '-p', str(port), '--startup',
                 '--test-profile', str(profile)], cwd=run_cwd, env=environment,
            )
            job.assign(process)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Owned EXE exited before readiness: %s' % process.returncode)
                try:
                    value = listing(port)
                except (OSError, ValueError):
                    time.sleep(0.1)
                    continue
                if (Path(value.get('path', '')).resolve() != share.resolve() or
                        Path(value.get('root', '')).resolve() != share.resolve()):
                    raise RuntimeError('Listener does not belong to this isolated share')
                break
            else:
                raise RuntimeError('Owned EXE did not become ready')

            directories = sorted(runtime_base.glob('_MEI*'))
            self.assertEqual(len(directories), 1, 'Expected one extraction under owned LOCALAPPDATA')
            extracted = directories[0]
            self.assertTrue(extracted.is_dir())
            self.assertFalse(extracted.is_symlink())
            self.assertEqual(extracted.resolve().parent, runtime_base.resolve())
            python_dlls = sorted(path for path in extracted.glob('python*.dll')
                                 if re.fullmatch(r'python\d{2,3}\.dll', path.name, re.IGNORECASE))
            self.assertTrue(python_dlls, 'No extracted Python runtime DLL')
            payload_files = python_dlls + [extracted / 'PyQt5/QtCore.pyd',
                                          extracted / 'PyQt5/Qt5/bin/Qt5Core.dll']
            for path in payload_files:
                self.assertTrue(path.is_file(), str(path))
                self.assertGreater(path.stat().st_size, 0)
            entry.update(extraction_directory=str(extracted),
                         extracted_payload_files=[{'file': path.relative_to(extracted).as_posix(),
                                                   'bytes': path.stat().st_size} for path in payload_files],
                         observed_share=str(share), ready=True)

            (profile / 'stop.request').write_text('stop\n', encoding='ascii')
            code = process.wait(timeout=20)
            entry['graceful_exit_code'] = code
            self.assertEqual(code, 0, 'Owned EXE did not exit normally')
            deadline = time.monotonic() + 5
            while extracted.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            entry['extraction_removed_before_fixture_cleanup'] = not extracted.exists()
            entry['remaining_runtime_entries'] = (
                sorted(path.name for path in runtime_base.iterdir()) if runtime_base.exists() else [])
            self.assertTrue(entry['extraction_removed_before_fixture_cleanup'])
            self.assertEqual(entry['remaining_runtime_entries'], [])
            entry['listener_closed'] = not port_is_open(port)
            self.assertTrue(entry['listener_closed'])
            entry['settings_ini_isolated'] = (profile / 'settings.ini').is_file()
            self.assertTrue(entry['settings_ini_isolated'])
            entry['portable_directory_files'] = sorted(path.name for path in self.portable.iterdir())
            self.assertEqual(entry['portable_directory_files'], ['lanshare.exe'])
            entry['success'] = True
            return entry
        except BaseException as exc:
            entry['error'] = '%s: %s' % (type(exc).__name__, exc)
            TEST_ENV.keep = True
            raise
        finally:
            try:
                if process is not None and process.poll() is None:
                    (profile / 'stop.request').write_text('stop\n', encoding='ascii')
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        entry['forced_owned_process_cleanup'] = True
                        TEST_ENV.stop_process(process)
            finally:
                job.close()
                entry['seconds'] = round(time.monotonic() - started, 3)

    def test_01_inherited_tool_temp_and_tmp(self):
        local = self.case_root / 'local-\u7528\u6237 \u914d\u7f6e'
        result = self.exercise('inherited', local)
        self.assertEqual(result['temp'], os.environ.get('TEMP'))
        self.assertEqual(result['tmp'], os.environ.get('TMP'))
        self.assertTrue(result['temporary_environment_inherited'])

    def test_02_unicode_local_app_data_and_temporary_paths(self):
        local = self.case_root / '\u7528\u6237 \u914d\u7f6e' / 'Local AppData'
        temporary = self.case_root / '\u4e34\u65f6 \u7a7a\u95f4'
        temporary.mkdir()
        self.exercise('unicode', local, {'TEMP': str(temporary), 'TMP': str(temporary)})
        self.assertEqual(list(temporary.glob('_MEI*')), [], 'Extraction incorrectly used TEMP/TMP')

    def test_03_invalid_tmp_does_not_control_extraction(self):
        temporary = self.case_root / 'valid-\u4e34\u65f6 \u76ee\u5f55'
        temporary.mkdir()
        sentinel = b'This file is not a temporary directory.\n'
        for kind in ('missing', 'ordinary-file'):
            with self.subTest(tmp_kind=kind):
                bad_tmp = self.case_root / ('invalid-' + kind)
                if kind == 'ordinary-file':
                    bad_tmp.write_bytes(sentinel)
                else:
                    bad_tmp = bad_tmp / 'absent-temp'
                    self.assertFalse(bad_tmp.exists())
                local = self.case_root / ('local-' + kind + ' \u7528\u6237')
                result = self.exercise(kind, local, {'TEMP': str(temporary), 'TMP': str(bad_tmp)})
                self.assertEqual(result['tmp'], str(bad_tmp))
                if kind == 'ordinary-file':
                    self.assertEqual(bad_tmp.read_bytes(), sentinel)
                else:
                    self.assertFalse(bad_tmp.exists(), 'The test or app repaired TMP instead of bypassing it')
                self.assertEqual(list(temporary.glob('_MEI*')), [])

    def test_04_repeated_launches_use_unique_and_removed_extractions(self):
        local = self.case_root / 'repeated-\u7528\u6237 \u914d\u7f6e'
        first = self.exercise('repeat-one', local)
        second = self.exercise('repeat-two', local)
        self.assertNotEqual(first['extraction_directory'], second['extraction_directory'])
        self.assertTrue(first['extraction_removed_before_fixture_cleanup'])
        self.assertTrue(second['extraction_removed_before_fixture_cleanup'])

    def test_05_original_executable_and_working_directory(self):
        local = self.case_root / 'original-local-\u7528\u6237 \u914d\u7f6e'
        result = self.exercise('original-location', local, executable=self.exe,
                               working_directory=self.exe.parent)
        self.assertTrue(result['original_executable_location'])
        self.assertEqual(result['working_directory'], str(self.exe.parent))

    def test_06_unicode_executable_directory_is_not_relocated(self):
        directory = self.case_root / '\u6d4b\u8bd5 \u76ee\u5f55' / '\u7b2c\u4e03\u7248'
        directory.mkdir(parents=True)
        executable = directory / 'lanshare.exe'
        shutil.copy2(self.exe, executable)
        local = self.case_root / 'unicode-exe-local-\u7528\u6237'
        result = self.exercise('unicode-executable', local, executable=executable,
                               working_directory=directory)
        self.assertEqual(result['executed_exe'], str(executable))
        self.assertEqual(result['working_directory'], str(directory))
        self.assertEqual(sorted(path.name for path in directory.iterdir()), ['lanshare.exe'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', required=True, type=Path)
    parser.add_argument('--results', type=Path,
                        default=ROOT / 'docs/changes/2026-10-28-startup-fix/test-results/startup-temp.json')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This regression requires Windows')
    try:
        exe = args.exe.resolve(strict=True)
        if not exe.is_file() or exe.suffix.lower() != '.exe':
            raise ValueError('--exe must be a standalone Windows EXE')
        verify_archive(exe)
    except Exception as exc:
        parser.error('Refusing to launch an unverified runtime-temp EXE: %s' % exc)
    StartupTempTests.exe = exe
    started = time.monotonic()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StartupTempTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    evidence = dict(StartupTempTests.evidence, success=result.wasSuccessful(), tests=result.testsRun,
                    failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                    seconds=round(time.monotonic() - started, 3),
                    timestamp=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    scope='Original and copied executable paths; owned Unicode LOCALAPPDATA and offscreen profiles; not the real Explorer environment',
                    count_policy='Six unittest methods; subtests and repeated launches are not extra methods')
    if not result.wasSuccessful():
        TEST_ENV.keep = True
        evidence['retained_owned_test_workspace'] = str(TEST_ENV.root)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(evidence, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    print('Startup temporary-directory evidence: %s' % args.results)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
