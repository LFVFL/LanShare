"""Verify default Windows onefile temp selection in owned first-run profiles."""
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

from PyInstaller.archive.readers import CArchiveReader  # noqa: E402

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')


def archive_options(exe):
    reader = CArchiveReader(str(exe))
    options = getattr(reader, 'options', None)
    if options is None:
        options = [name for name, entry in reader.toc.items() if entry[-1] == 'OPTION']
    return options


def verify_archive(exe):
    options = archive_options(exe)
    runtime_options = [option for option in options if option.startswith('pyi-runtime-tmpdir')]
    if runtime_options:
        raise ValueError('Custom runtime temp options are forbidden; expected the Windows default')
    return runtime_options


def path_kind(path):
    try:
        if path.is_symlink():
            return 'symlink'
        if path.is_dir():
            return 'directory'
        if path.is_file():
            return 'file'
        return 'other' if path.exists() else 'missing'
    except (FileNotFoundError, NotADirectoryError):
        return 'missing'


def local_app_data_state(value):
    if value is None:
        return {'environment': 'unset'}
    if value == '':
        return {'environment': 'empty'}
    local = Path(value)
    return {'environment': 'set', 'local_app_data': path_kind(local),
            'lanshare': path_kind(local / 'LanShare'),
            'legacy_temp': path_kind(local / 'LanShare' / 'Temp')}


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
        archive_options = verify_archive(cls.exe)
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
                            size_bytes=cls.exe.stat().st_size,
                            runtime_directory_policy='pyinstaller-default-windows-temp',
                            runtime_tmpdir_archive_options=archive_options,
                            runs=[], local_app_data_fixture_policy='owned-paths-or-unset-or-empty')

    def setUp(self):
        self.case_root = TEST_ENV.temporary_dir(prefix='startup-case-')

    def make_temporary_paths(self, label):
        temporary = self.case_root / ('temp-' + label)
        tmp = self.case_root / ('tmp-' + label)
        temporary.mkdir()
        tmp.mkdir()
        return temporary, tmp

    def exercise(self, label, local_app_data, local_kind='directory', lanshare_kind='missing',
                 temporary_paths=None, tmp_unset=False, executable=None, working_directory=None):
        run_exe = Path(executable or self.copied_exe).resolve(strict=True)
        run_cwd = Path(working_directory or self.portable).resolve(strict=True)
        self.assertTrue(run_exe.is_file())
        self.assertTrue(run_cwd.is_dir())
        self.assertEqual(hashlib.sha256(run_exe.read_bytes()).hexdigest(), self.evidence['sha256'])
        local_before = local_app_data_state(local_app_data)
        if local_app_data not in (None, ''):
            self.assertEqual(local_before['local_app_data'], local_kind)
            self.assertEqual(local_before['lanshare'], lanshare_kind)
            self.assertEqual(local_before['legacy_temp'], 'missing')
        temporary, tmp = temporary_paths or self.make_temporary_paths(label)
        temporary, tmp = Path(temporary).resolve(strict=True), Path(tmp).resolve(strict=True)
        self.assertNotEqual(temporary, tmp)
        for directory in (temporary, tmp):
            self.assertTrue(directory.is_dir())
            self.assertFalse(directory.is_symlink())
            directory.relative_to(self.case_root)
            self.assertEqual(list(directory.iterdir()), [], 'Owned temp must be empty before launch')
        runtime_base = temporary if tmp_unset else tmp
        profile = self.case_root / ('profile-' + label)
        profile.mkdir()
        share = profile / 'share'
        share.mkdir()
        (share / 'owned.txt').write_text('Synthetic startup fixture.\n', encoding='ascii')
        environment = dict(os.environ)
        for key in tuple(environment):
            name = key.upper()
            if (name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX',
                         'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QTDIR',
                         'LOCALAPPDATA', 'TEMP', 'TMP') or
                    name.startswith(('_PYI_', 'PYINSTALLER_'))):
                environment.pop(key)
        windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        environment.update(QT_QPA_PLATFORM='offscreen', TEMP=str(temporary),
                           LANSHARE_DATA_DIR=str(profile / 'chat_data'),
                           PYINSTALLER_RESET_ENVIRONMENT='1',
                           PATH=os.pathsep.join((str(windows / 'System32'), str(windows))))
        if local_app_data is not None:
            environment['LOCALAPPDATA'] = str(local_app_data)
        if not tmp_unset:
            environment['TMP'] = str(tmp)
        self.assertEqual(sorted(key for key in environment
                                if key.upper().startswith(('_PYI_', 'PYINSTALLER_'))),
                         ['PYINSTALLER_RESET_ENVIRONMENT'])
        entry = dict(case=label, local_app_data=environment.get('LOCALAPPDATA'),
                     local_app_data_state_before_launch=local_before,
                     owned_temp_directories=[str(temporary), str(tmp)],
                     owned_temp_directories_empty_before_launch=True, runtime_base=str(runtime_base),
                     executed_exe=str(run_exe), working_directory=str(run_cwd),
                     original_executable_location=run_exe == self.exe,
                     temp=environment.get('TEMP'), tmp=environment.get('TMP'),
                     tmp_environment_unset=tmp_unset,
                     expected_temp_source='TEMP' if tmp_unset else 'TMP',
                     bootloader_environment_reset=True, launched=False, success=False)
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
            entry['launched'] = True
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
            self.assertEqual(len(directories), 1, 'Expected one extraction under the selected owned temp')
            extracted = directories[0]
            self.assertTrue(extracted.is_dir())
            self.assertFalse(extracted.is_symlink())
            self.assertEqual(extracted.resolve().parent, runtime_base.resolve())
            for directory in (temporary, tmp):
                if directory != runtime_base:
                    self.assertEqual(list(directory.glob('_MEI*')), [], 'Unselected temp was used')
            self.assertEqual(local_app_data_state(local_app_data), local_before,
                             'Default extraction must not create legacy LOCALAPPDATA directories')
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
            entry['remaining_owned_temp_entries'] = {
                str(directory): sorted(path.name for path in directory.iterdir())
                for directory in (temporary, tmp)}
            self.assertTrue(all(not entries for entries in entry['remaining_owned_temp_entries'].values()))
            entry['local_app_data_state_after_exit'] = local_app_data_state(local_app_data)
            self.assertEqual(entry['local_app_data_state_after_exit'], local_before)
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

    def test_01_first_launch_without_lanshare_directory(self):
        local = self.case_root / 'local'
        local.mkdir()
        self.assertFalse((local / 'LanShare').exists())
        self.exercise('first-launch', local)

    def test_02_first_launch_without_legacy_temp_directory(self):
        local = self.case_root / 'local'
        (local / 'LanShare').mkdir(parents=True)
        self.assertFalse((local / 'LanShare' / 'Temp').exists())
        self.exercise('missing-legacy-temp', local, lanshare_kind='directory')

    def test_03_local_app_data_unset_and_empty(self):
        for label, value in (('local-unset', None), ('local-empty', '')):
            with self.subTest(local_app_data=label):
                result = self.exercise(label, value)
                self.assertEqual(result['local_app_data'], value)

    def test_04_nonexistent_local_app_data(self):
        local = self.case_root / 'missing-profile' / 'AppData' / 'Local'
        self.assertFalse(local.parent.exists())
        self.exercise('nonexistent-local', local, local_kind='missing')
        self.assertFalse(local.parent.exists())

    def test_05_local_app_data_with_ordinary_file_ancestor(self):
        blocker = self.case_root / 'profile-blocker'
        sentinel = b'This file is not a LOCALAPPDATA directory.\n'
        blocker.write_bytes(sentinel)
        self.assertTrue(blocker.is_file())
        result = self.exercise('file-ancestor-local', blocker / 'Local', local_kind='missing')
        self.assertEqual(blocker.read_bytes(), sentinel)
        result['local_app_data_file_ancestor'] = str(blocker)
        result['local_app_data_file_ancestor_unchanged'] = True

    def test_06_unicode_local_app_data_and_temporary_paths(self):
        local = self.case_root / '\u7528\u6237 \u914d\u7f6e' / 'Local AppData'
        local.mkdir(parents=True)
        temporary_paths = self.make_temporary_paths('\u4e34\u65f6 \u7a7a\u95f4')
        self.exercise('unicode', local, temporary_paths=temporary_paths)

    def test_07_unset_tmp_falls_back_to_valid_temp(self):
        local = self.case_root / 'local'
        local.mkdir()
        result = self.exercise('unset-tmp', local, tmp_unset=True)
        self.assertIsNone(result['tmp'])
        self.assertEqual(result['runtime_base'], result['temp'])

    def test_08_repeated_launches_use_unique_and_removed_extractions(self):
        local = self.case_root / 'repeated-\u7528\u6237 \u914d\u7f6e'
        local.mkdir()
        temporary_paths = self.make_temporary_paths('repeated')
        first = self.exercise('repeat-one', local, temporary_paths=temporary_paths)
        second = self.exercise('repeat-two', local, temporary_paths=temporary_paths)
        self.assertEqual(first['runtime_base'], second['runtime_base'])
        self.assertNotEqual(first['extraction_directory'], second['extraction_directory'])
        self.assertTrue(first['extraction_removed_before_fixture_cleanup'])
        self.assertTrue(second['extraction_removed_before_fixture_cleanup'])

    def test_09_original_executable_and_working_directory(self):
        local = self.case_root / 'original-local-\u7528\u6237 \u914d\u7f6e'
        local.mkdir()
        result = self.exercise('original-location', local, executable=self.exe,
                               working_directory=self.exe.parent)
        self.assertTrue(result['original_executable_location'])
        self.assertEqual(result['working_directory'], str(self.exe.parent))

    def test_10_unicode_executable_directory_is_not_relocated(self):
        directory = self.case_root / '\u6d4b\u8bd5 \u76ee\u5f55' / '\u7b2c\u4e03\u7248'
        directory.mkdir(parents=True)
        executable = directory / 'lanshare.exe'
        shutil.copy2(self.exe, executable)
        local = self.case_root / 'unicode-exe-local-\u7528\u6237'
        local.mkdir()
        result = self.exercise('unicode-executable', local, executable=executable,
                               working_directory=directory)
        self.assertEqual(result['executed_exe'], str(executable))
        self.assertEqual(result['working_directory'], str(directory))
        self.assertEqual(sorted(path.name for path in directory.iterdir()), ['lanshare.exe'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', required=True, type=Path)
    parser.add_argument('--results', required=True, type=Path,
                        help='New JSON evidence file; existing files are never overwritten')
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
    try:
        args.results.parent.mkdir(parents=True, exist_ok=True)
        output = args.results.open('x', encoding='utf-8')
    except OSError as exc:
        parser.error('Cannot reserve new --results file; no EXE was launched: %s' % exc)
    with output:
        started = time.monotonic()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(StartupTempTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        evidence = dict(StartupTempTests.evidence, success=result.wasSuccessful(), tests=result.testsRun,
                        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                        planned_launches=12,
                        attempted_launches=len(StartupTempTests.evidence.get('runs', [])),
                        launches=sum(bool(run.get('launched'))
                                     for run in StartupTempTests.evidence.get('runs', [])),
                        successful_launches=sum(bool(run.get('success'))
                                                for run in StartupTempTests.evidence.get('runs', [])),
                        seconds=round(time.monotonic() - started, 3),
                        timestamp=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                        scope='Original and copied executable paths; owned TEMP/TMP and simulated LOCALAPPDATA states; offscreen profiles, not a fresh Windows user or Explorer double-click',
                        count_policy='Ten unittest methods; a complete successful matrix has twelve launches, with subtests and repeated launches not counted as extra methods',
                        limitations=['An invalid or unwritable TMP/TEMP is not guaranteed to fall back; such cases are not launched by this matrix.'])
        if not result.wasSuccessful():
            TEST_ENV.keep = True
            evidence['retained_owned_test_workspace'] = str(TEST_ENV.root)
        output.write(json.dumps(evidence, ensure_ascii=True, indent=2) + '\n')
    print('Startup temporary-directory evidence: %s' % args.results)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
