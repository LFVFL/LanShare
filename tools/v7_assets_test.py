"""Cache-version regressions for every independently served asset bundle."""
import contextlib
import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from test_support import isolate_test_environment

TEST_ENV = isolate_test_environment()
from server import pages

RESULTS = ROOT / 'docs' / 'changes' / '2026-10-27' / 'test-results'
NAMES = ('CSS', 'JS', 'JS_FILES', 'JS_CHAT', 'JS_LOGIN', 'THEME_BOOT')


class AssetVersionTests(unittest.TestCase):
    def version(self, values):
        with patch.object(pages, 'webassets', SimpleNamespace(**values)):
            return pages._asset_version()

    def test_optional_file_chat_login_bundle_changes_invalidate_url(self):
        values = {name: 'synthetic-' + name for name in NAMES}
        before = self.version(values)
        for name in ('JS_FILES', 'JS_CHAT', 'JS_LOGIN'):
            with self.subTest(bundle=name):
                changed = dict(values, **{name: values[name] + '-changed'})
                self.assertNotEqual(self.version(changed), before)

    def test_core_css_and_theme_changes_still_invalidate_url(self):
        values = {name: 'synthetic-' + name for name in NAMES}
        before = self.version(values)
        for name in ('CSS', 'JS', 'THEME_BOOT'):
            with self.subTest(bundle=name):
                self.assertNotEqual(self.version(dict(values, **{name: ''})), before)

    def test_tags_and_lengths_separate_empty_bundles_and_boundaries(self):
        values = {name: '' for name in NAMES}
        self.assertNotEqual(
            self.version(dict(values, CSS='ab', JS='c')),
            self.version(dict(values, CSS='a', JS='bc')),
        )
        self.assertNotEqual(
            self.version(dict(values, JS_FILES='', JS_CHAT='one-byte')),
            self.version(dict(values, JS_FILES='one-byte', JS_CHAT='')),
        )
        self.assertNotEqual(
            self.version(dict(values, JS_CHAT='', JS_LOGIN='login')),
            self.version(dict(values, JS_CHAT='login', JS_LOGIN='')),
        )

    def test_same_contents_and_missing_empty_attributes_are_stable(self):
        values = {name: 'synthetic-' + name for name in NAMES}
        self.assertEqual(self.version(values), self.version(dict(reversed(list(values.items())))))
        self.assertEqual(self.version({}), self.version({name: '' for name in NAMES}))
        self.assertEqual(len(self.version(values)), 10)

    def test_no_assets_uses_zero_version(self):
        with patch.object(pages, 'webassets', None):
            self.assertEqual(pages._asset_version(), '0')


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


if __name__ == '__main__':
    RESULTS.mkdir(parents=True, exist_ok=True)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        result = unittest.TextTestRunner(stream=output, verbosity=2,
            resultclass=RecordedResults).run(unittest.defaultTestLoader.loadTestsFromTestCase(AssetVersionTests))
    log = output.getvalue().replace(str(TEST_ENV.root), '<test-root>').replace(str(ROOT), '<checkout>')
    (RESULTS / 'assets.log').write_text(log, encoding='utf-8')
    report = {
        'timestamp': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'runtime': sys.version.split()[0],
        'environment': 'virtualenv' if sys.prefix != sys.base_prefix else 'system',
        'tests': result.testsRun, 'success': result.wasSuccessful(),
        'failures': len(result.failures), 'errors': len(result.errors),
        'cases': result.cases,
    }
    (RESULTS / 'assets.json').write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n',
                                      encoding='utf-8')
    print(log)
    raise SystemExit(0 if result.wasSuccessful() else 1)
