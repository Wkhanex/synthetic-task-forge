import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from task_forge.cli import main
from task_forge.core import PackageError, build, check_package, evaluate, filename, pointer, render, validate_manifest

ROOT = Path(__file__).resolve().parents[1]

def example():
    return json.loads((ROOT / 'examples/manifest.json').read_text())

class ForgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_valid_manifest_and_expected_artifacts(self):
        result = build(validate_manifest(example()), self.root/'pack')
        self.assertEqual(3, result['solver_files'])
        self.assertTrue(all(r['passed'] for r in check_package(self.root/'pack')))

    def test_reviewer_data_is_excluded_from_solver_zip(self):
        build(example(), self.root/'pack')
        with zipfile.ZipFile(self.root/'pack/input_files.zip') as archive:
            self.assertEqual({'prompt.md', 'invoices.csv', 'customers.csv', 'operations-note.txt'}, set(archive.namelist()))
            self.assertFalse(any(b'GOLD_SOLUTION' in archive.read(name) for name in archive.namelist()))
        self.assertTrue((self.root/'pack/reviewer/review-answer.json').exists())

    def test_zip_bytes_are_reproducible(self):
        build(example(), self.root/'a')
        build(example(), self.root/'b')
        self.assertEqual((self.root/'a/input_files.zip').read_bytes(), (self.root/'b/input_files.zip').read_bytes())

    def test_existing_output_is_preserved(self):
        output = self.root/'pack'
        output.mkdir()
        (output/'keep.txt').write_text('keep')
        with self.assertRaises(PackageError): build(example(), output)
        self.assertEqual('keep', (output/'keep.txt').read_text())

    def test_failed_invariant_prevents_output(self):
        data = example()
        data['invariants'][0]['expected'] = '0.00'
        with self.assertRaisesRegex(PackageError, 'total'): build(data, self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_solver_content_leakage_prevents_build(self):
        data = example()
        data['files'][2]['content'] += ' GOLD_SOLUTION'
        with self.assertRaisesRegex(PackageError, 'audience:operations-note'): build(data, self.root/'bad')

    def test_prompt_leakage_prevents_build(self):
        data = example()
        data['prompt'] += ' internal_grading_only'
        with self.assertRaisesRegex(PackageError, 'audience:prompt'): build(data, self.root/'bad')

    def test_duplicate_filenames_and_columns_are_rejected(self):
        data = example()
        data['files'][1]['name'] = 'INVOICES.csv'
        with self.assertRaises(PackageError): validate_manifest(data)
        data = example()
        data['files'][0]['columns'].append('amount')
        with self.assertRaises(PackageError): validate_manifest(data)

    def test_unsafe_names_and_nested_archives_are_rejected(self):
        for name in ('../outside', '/root.txt', 'a/b.csv', 'a\\b.csv', '.hidden', 'prompt.md', 'nested.zip'):
            with self.subTest(name=name), self.assertRaises(PackageError): filename(name)

    def test_csv_shape_must_match_columns(self):
        data = example()
        del data['files'][0]['rows'][0]['amount']
        with self.assertRaisesRegex(PackageError, 'row keys'): validate_manifest(data)

    def test_unknown_invariant_file_and_duplicate_ids_are_rejected(self):
        for change in ('file', 'id', 'kind'):
            data = example()
            if change == 'file': data['invariants'][0]['file'] = 'missing.csv'
            if change == 'id': data['invariants'][1]['id'] = 'total'
            if change == 'kind': data['invariants'][0]['kind'] = 'execute_python'
            with self.assertRaises(PackageError): validate_manifest(data)

    def test_exact_decimal_sum(self):
        data = example()
        for row, value in zip(data['files'][0]['rows'], ['0.1', '0.2', '0.0']): row['amount'] = value
        data['invariants'][0]['expected'] = '0.3'
        self.assertTrue(evaluate(data, {a['name']: render(a) for a in data['files']})[0]['passed'])

    def test_numeric_sum_rejects_nonfinite_or_currency_symbols(self):
        for value in ('NaN', 'Infinity', '£1.00', '1e3'):
            data = example()
            data['files'][0]['rows'][0]['amount'] = value
            self.assertFalse(evaluate(data, {a['name']: render(a) for a in data['files']})[0]['passed'])

    def test_foreign_key_and_unique_invariants_detect_errors(self):
        data = example()
        data['files'][0]['rows'][0]['customer_id'] = 'MISSING'
        data['files'][0]['rows'][1]['invoice_id'] = 'I-101'
        rows = {r['id']: r for r in evaluate(data, {a['name']: render(a) for a in data['files']})}
        self.assertFalse(rows['invoice_ids']['passed'])
        self.assertFalse(rows['customer_links']['passed'])

    def test_json_pointer_escaping_and_type_sensitive_equality(self):
        self.assertEqual(3, pointer({'a/b': {'~value': [3]}}, '/a~1b/~0value/0'))
        with self.assertRaises(PackageError): pointer({}, 'bad')
        with self.assertRaises(PackageError): pointer({}, '/missing')
        data = example()
        data['files'][3]['content']['total'] = 1200
        data['invariants'][3]['expected'] = '1200'
        self.assertFalse(evaluate(data, {a['name']: render(a) for a in data['files']})[3]['passed'])

    def test_tampering_and_missing_artifacts_fail_recheck(self):
        build(example(), self.root/'pack')
        p = self.root/'pack/inputs/invoices.csv'
        p.write_text(p.read_text().replace('800.00', '900.00'))
        self.assertTrue(any(not r['passed'] for r in check_package(self.root/'pack')))
        p.unlink()
        self.assertTrue(any(not r['passed'] for r in check_package(self.root/'pack')))

    def test_symlink_artifacts_are_not_followed(self):
        build(example(), self.root/'pack')
        outside = self.root/'outside.csv'
        outside.write_text('outside')
        p = self.root/'pack/inputs/invoices.csv'
        p.unlink()
        p.symlink_to(outside)
        with self.assertRaises(PackageError): check_package(self.root/'pack')

    def test_review_artifact_cannot_enter_delivery_archive(self):
        build(example(), self.root/'pack')
        with zipfile.ZipFile(self.root/'pack/input_files.zip', 'a') as archive: archive.writestr('review-answer.json', '{}')
        self.assertFalse(check_package(self.root/'pack')[-1]['passed'])

    def test_cli_success_and_failure_codes(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, main(['validate', str(ROOT/'examples/manifest.json')]))
            self.assertEqual(0, main(['build', str(ROOT/'examples/manifest.json'), '--output', str(self.root/'pack')]))
            self.assertEqual(0, main(['check', str(self.root/'pack')]))
            (self.root/'pack/inputs/invoices.csv').write_text('bad')
            self.assertEqual(1, main(['check', str(self.root/'pack')]))
            self.assertEqual(2, main(['validate', str(self.root/'missing.json')]))

    def test_missing_checksum_inventory_cannot_pass(self):
        build(example(), self.root/'pack')
        (self.root/'pack/reviewer/checksums.json').write_text('{}')
        checks = {r['id']: r for r in check_package(self.root/'pack')}
        self.assertFalse(checks['hash:inventory']['passed'])

    def test_nested_json_values_are_type_sensitive(self):
        data = example()
        data['files'][3]['content'] = {'flag': True}
        data['invariants'][3].update(pointer='', expected={'flag': 1})
        self.assertFalse(evaluate(data, {a['name']: render(a) for a in data['files']})[3]['passed'])

    def test_reserved_reviewer_names_and_invalid_pointer_escapes(self):
        for name in ('CHECKSUMS.json', 'check-results.json'):
            with self.assertRaises(PackageError): filename(name)
        with self.assertRaises(PackageError): pointer({'a~2b': 3}, '/a~2b')

if __name__ == '__main__': unittest.main()
