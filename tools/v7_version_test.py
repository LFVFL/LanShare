"""Check v7 version surfaces in an isolated offscreen GUI and optional onefile.

    python -B tools/v7_version_test.py
    python -B tools/v7_version_test.py --source E:\\LanShare-builds\\build-example \
        --exe E:\\LanShare-builds\\build-example\\dist\\lanshare.exe

--exe reads the PyInstaller archive; it never starts or extracts the EXE.
Use a new --results path for a subsequent run; existing evidence is refused.
"""
import argparse
import contextlib
import dis
import hashlib
import io
import json
import marshal
import os
from pathlib import Path
import re
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest import mock


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
CONTEXT = None
VERSION_PATTERN = re.compile(r"\bLan\.version\s*=\s*(['\"])([^'\"]+)\1")


def digest(path):
    with Path(path).open('rb') as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
        return value.hexdigest()


def code_value(value):
    if isinstance(value, types.CodeType):
        fields = ('co_name', 'co_argcount', 'co_posonlyargcount',
                  'co_kwonlyargcount', 'co_nlocals', 'co_stacksize', 'co_flags',
                  'co_code', 'co_consts', 'co_names', 'co_varnames',
                  'co_freevars', 'co_cellvars')
        fields = tuple(name for name in fields if hasattr(value, name))
        for optional in ('co_qualname', 'co_exceptiontable'):
            if hasattr(value, optional):
                fields += (optional,)
        return {'code': {name: code_value(getattr(value, name)) for name in fields}}
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    if isinstance(value, tuple):
        return {'tuple': [code_value(item) for item in value]}
    if isinstance(value, frozenset):
        items = [code_value(item) for item in value]
        return {'frozenset': sorted(items, key=lambda item: json.dumps(item, sort_keys=True))}
    if value is Ellipsis:
        return {'ellipsis': True}
    if isinstance(value, complex):
        return {'complex': [value.real, value.imag]}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError('Unsupported code constant: %s' % type(value).__name__)


def code_fingerprint(code):
    # Ignore source paths/line tables; retain executable instructions and constants.
    serialized = json.dumps(code_value(code), ensure_ascii=True, sort_keys=True).encode('ascii')
    return hashlib.sha256(serialized).hexdigest()


def assigned_literal(code, name):
    previous = None
    found = []
    for instruction in dis.get_instructions(code):
        if instruction.opname in ('STORE_NAME', 'STORE_GLOBAL') and instruction.argval == name:
            if previous is None or previous.opname != 'LOAD_CONST':
                raise ValueError('%s is not a direct literal assignment' % name)
            found.append(previous.argval)
        previous = instruction
    if len(found) != 1:
        raise ValueError('Expected one literal assignment for %s' % name)
    return found[0]


@contextlib.contextmanager
def owned_gui(name):
    context = CONTEXT
    settings = context.QSettings(str(context.environment.root / (name + '.ini')),
                                 context.QSettings.IniFormat)
    with mock.patch.object(context.gui_module.QSystemTrayIcon, 'isSystemTrayAvailable', return_value=False):
        window = context.environment.gui(auto_start=False, settings=settings)
    try:
        context.app.processEvents()
        if window.sharer.is_running or window.sharer.is_busy:
            raise AssertionError('Version-only GUI must never start a service')
        if window._system_integration_enabled or window._context_menu.registry is not None:
            raise AssertionError('Version-only GUI must disable system integration')
        Path(settings.fileName()).resolve().relative_to(context.environment.root)
        yield window
    finally:
        window.sharer.stop()
        window.sharer.wait_stopped(5)
        window.tray_icon.hide()
        window.close()
        window.deleteLater()
        context.app.sendPostedEvents(None, context.QEvent.DeferredDelete)
        context.app.processEvents()
        settings.sync()


class VersionTests(unittest.TestCase):
    def test_shared_python_version_is_expected(self):
        context = CONTEXT
        self.assertEqual(context.server.APP_VERSION, context.expected)
        self.assertEqual(context.gui_module.APP_VERSION, context.server.APP_VERSION)
        self.assertEqual(context.entry.APP_VERSION, context.server.APP_VERSION)
        context.evidence['python_version'] = context.server.APP_VERSION

    def test_requested_source_tree_is_actually_imported(self):
        context = CONTEXT
        modules = (context.server, context.gui_module, context.entry, context.assets)
        for module in modules:
            with self.subTest(module=module.__name__):
                Path(module.__file__).resolve().relative_to(context.source)

    def test_window_title_and_header_show_v7(self):
        context = CONTEXT
        with owned_gui('version') as window:
            label = window.findChild(context.QLabel, 'ver')
            self.assertIsNotNone(label)
            context.evidence['gui'] = {'title': window.windowTitle(), 'header': label.text(),
                'settings': '<test-root>/version.ini', 'auto_start': window.auto_start,
                'system_integration_enabled': window._system_integration_enabled,
                'service_running': window.sharer.is_running}
            self.assertEqual(window.windowTitle(), '\u5c40\u57df\u7f51\u4e91\u76d8 v' + context.expected)
            self.assertEqual(label.text(), 'v' + context.expected)

    def test_both_gui_surfaces_read_the_shared_constant(self):
        context = CONTEXT
        with mock.patch.object(context.gui_module, 'APP_VERSION', '99.1'):
            with owned_gui('version-dynamic') as window:
                label = window.findChild(context.QLabel, 'ver')
                self.assertIsNotNone(label)
                self.assertEqual(window.windowTitle(), '\u5c40\u57df\u7f51\u4e91\u76d8 v99.1')
                self.assertEqual(label.text(), 'v99.1')
        self.assertEqual(context.gui_module.APP_VERSION, context.expected)

    def test_entrypoint_sets_qt_application_version(self):
        context = CONTEXT
        entry, app = context.entry, context.app
        previous = (app.applicationName(), app.organizationName(), app.applicationVersion())
        app.setApplicationVersion('version-test-unset')
        try:
            with mock.patch.object(entry, 'QApplication', return_value=app), \
                    mock.patch.object(entry, 'SingleInstance') as instance, \
                    mock.patch.object(entry, 'FileShareGUI') as gui, \
                    mock.patch.object(entry, 'watch_test_exit'), \
                    mock.patch.object(entry, 'start_zip_cleanup_thread'), \
                    mock.patch.object(entry, 'build_app_icon', return_value=None), \
                    mock.patch.object(entry, 'set_app_icon'), \
                    mock.patch.object(entry.platform, 'system', return_value='IsolatedTest'), \
                    mock.patch.object(app, 'exec_', return_value=0), \
                    mock.patch.object(sys, 'argv', ['lanshare.py', '--test-profile', str(context.environment.root)]):
                instance.return_value.acquire.return_value = True
                self.assertEqual(entry.main(), 0)
                self.assertEqual(app.applicationVersion(), context.expected)
                self.assertFalse(gui.call_args.kwargs['system_integration_enabled'])
                ini = Path(gui.call_args.kwargs['settings'].fileName()).resolve()
                self.assertEqual(ini, context.environment.root / 'settings.ini')
                instance.return_value.close.assert_called_once()
                context.evidence['qt_application_version'] = app.applicationVersion()
        finally:
            app.setApplicationName(previous[0])
            app.setOrganizationName(previous[1])
            app.setApplicationVersion(previous[2])

    def test_browser_bundle_and_available_source_version(self):
        context = CONTEXT
        bundle = VERSION_PATTERN.findall(context.assets.JS)
        self.assertEqual([value for quote, value in bundle], [context.expected])
        source_file = context.source / 'web/js/00-core.js'
        source_version = None
        if source_file.is_file():
            source_version = [value for quote, value in VERSION_PATTERN.findall(source_file.read_text(encoding='utf-8'))]
            self.assertEqual(source_version, [context.expected])
        context.evidence['browser'] = {'bundle_version': bundle[0][1],
            'core_source_version': source_version,
            'source_note': 'Build snapshots intentionally omit the original web tree; generated webassets.JS is always checked.'}


class ArchiveVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(CONTEXT.exe))
        pyz_names = [name for name in archive.toc if name.lower().endswith('.pyz')]
        if len(pyz_names) != 1:
            raise RuntimeError('Expected one embedded PYZ')
        pyz = archive.open_embedded_archive(pyz_names[0])
        cls.codes = {name: pyz.extract(name) for name in ('server', 'ui.main_window', 'webassets')}
        cls.codes['lanshare'] = marshal.loads(archive.extract('lanshare'))
        if not all(isinstance(value, types.CodeType) for value in cls.codes.values()):
            raise RuntimeError('Required packaged modules are missing')
        CONTEXT.evidence['archive'] = {'exe_started': False, 'extracted_to_disk': False,
            'method': 'Read-only CArchive/PYZ parsing and unmarshalling; no packaged code is executed',
            'pyz_member': pyz_names[0], 'modules': sorted(cls.codes)}

    def test_packaged_server_version_is_expected(self):
        value = assigned_literal(self.codes['server'], 'APP_VERSION')
        CONTEXT.evidence['archive']['python_version'] = value
        self.assertEqual(value, CONTEXT.expected)

    def test_packaged_desktop_code_matches_the_gui_tested_snapshot(self):
        context = CONTEXT
        pairs = [('server', 'server/__init__.py'), ('ui.main_window', 'ui/main_window.py'),
                 ('lanshare', 'lanshare.py')]
        observations = {}
        context.evidence['archive']['source_correspondence'] = observations
        for module, relative_path in pairs:
            with self.subTest(module=module):
                compiled = compile(context.source_bytes[relative_path], relative_path, 'exec',
                                   dont_inherit=True, optimize=0)
                source_hash = code_fingerprint(compiled)
                packaged_hash = code_fingerprint(self.codes[module])
                observations[module] = {'source_semantic_sha256': source_hash,
                    'packaged_semantic_sha256': packaged_hash, 'match': source_hash == packaged_hash}
                self.assertEqual(packaged_hash, source_hash)

    def test_packaged_browser_version_and_bundle_match_source(self):
        value = assigned_literal(self.codes['webassets'], 'JS')
        self.assertIsInstance(value, str)
        versions = [version for quote, version in VERSION_PATTERN.findall(value)]
        CONTEXT.evidence['archive']['browser_version'] = versions
        self.assertEqual(versions, [CONTEXT.expected])
        self.assertEqual(value, CONTEXT.assets.JS)


class RecordedResults(unittest.TextTestResult):
    def __init__(self, *arguments, **keywords):
        super().__init__(*arguments, **keywords)
        self.cases = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append({'name': test.id(), 'status': 'passed'})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.cases.append({'name': test.id(), 'status': 'failed'})

    def addError(self, test, error):
        super().addError(test, error)
        self.cases.append({'name': test.id(), 'status': 'error'})


def main():
    global CONTEXT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--exe', type=Path, help='Optional onefile for read-only version inspection')
    parser.add_argument('--expected-version', default='7.0.1')
    parser.add_argument('--results', type=Path, default=ROOT / 'docs/changes/2026-10-28-release-v7/test-results/version.json')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    exe = args.exe.resolve(strict=True) if args.exe else None
    report_path = args.results.resolve()
    log_path = report_path.with_suffix('.log')
    if report_path.exists() or log_path.exists():
        parser.error('Choose a new result path; existing evidence is not overwritten')
    names = ['server/__init__.py', 'ui/main_window.py', 'lanshare.py', 'webassets.py']
    if (source / 'web/js/00-core.js').is_file():
        names.append('web/js/00-core.js')
    source_bytes = {name: (source / name).read_bytes() for name in names}
    before_hashes = {name: hashlib.sha256(value).hexdigest() for name, value in source_bytes.items()}
    exe_before = digest(exe) if exe else None
    from test_support import isolate_test_environment
    environment = isolate_test_environment()
    sys.path.insert(0, str(source))
    from PyQt5.QtCore import QEvent, QSettings
    from PyQt5.QtWidgets import QApplication, QLabel
    import server
    import ui.main_window as gui_module
    import lanshare as entry
    import webassets as assets
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    CONTEXT = types.SimpleNamespace(source=source, exe=exe, expected=args.expected_version,
        environment=environment, app=app, QSettings=QSettings, QEvent=QEvent, QLabel=QLabel,
        server=server, gui_module=gui_module, entry=entry, assets=assets,
        source_bytes=source_bytes, evidence={})
    output = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(VersionTests)
    if exe:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ArchiveVersionTests))
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = unittest.TextTestRunner(stream=output, verbosity=2,
                resultclass=RecordedResults).run(suite)
    finally:
        app.processEvents()
        environment.cleanup()
    after_hashes = {name: digest(source / name) for name in names}
    unchanged = before_hashes == after_hashes
    exe_unchanged = exe is None or digest(exe) == exe_before
    report = {'timestamp': datetime.now(timezone.utc).astimezone().isoformat(),
        'runtime': sys.version.split()[0], 'source': str(source), 'expected_version': args.expected_version,
        'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': len(result.skipped), 'cases': result.cases,
        'success': result.wasSuccessful() and unchanged and exe_unchanged and not environment.root.exists(),
        'evidence': CONTEXT.evidence, 'source_sha256': before_hashes,
        'source_unchanged': unchanged, 'exe': str(exe) if exe else None,
        'exe_sha256': exe_before, 'exe_unchanged': exe_unchanged,
        'fixture_removed': not environment.root.exists(),
        'scope': 'Offscreen Qt, independent INI and synthetic chat data. GUI auto-start/system integration disabled. Entry-point IPC, cleanup worker and event loop are mocked.',
        'limitations': ['The optional onefile inspection is static correspondence, not an EXE launch or Explorer interaction.',
                        'Windows PE file properties are outside this title/header version check.']}
    log = output.getvalue().replace(str(environment.root), '<test-root>').replace(str(source), '<source>')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=True, indent=2)
        stream.write('\n')
    with log_path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(log)
    print(log)
    print(json.dumps({'success': report['success'], 'tests': report['tests'],
                      'results': str(report_path)}, ensure_ascii=True))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
