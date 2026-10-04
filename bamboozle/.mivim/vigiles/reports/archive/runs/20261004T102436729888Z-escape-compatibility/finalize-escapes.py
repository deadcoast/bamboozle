"""Publish qualification only after checking current source, artifacts and retained contexts."""
import json
import runpy
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from bamboozle import __version__, service
from bamboozle.dictionary import Dictionary
from bamboozle.evidence import report_destination, write_report
from bamboozle.formats import DocumentCodec, FormatRules
from bamboozle.project_views import blob, load_view, manifest_marker_version
from bamboozle.return_validation import validate_return
from bamboozle.utils import sha256, utc_now
from bamboozle.view_import import view_status
from bamboozle.workspace import Workspace

root = Path.cwd().resolve()
assert __version__ == '0.4.9'
verification_path = report_destination(root, 'VIGILES_VERIFICATION.json')
verification = json.loads(verification_path.read_bytes())
assert verification['status'] == 'passed'
assert len(verification['steps']) == 11 and all(step['returncode'] == 0 for step in verification['steps'])
assert verification['tests'] == {'tests': 842, 'failures': 0, 'errors': 0, 'skipped': 0}
for name, expected in verification['source_sha256'].items():
    assert sha256((root / name).read_bytes()) == expected, name
artifacts = {}
for name in verification['required_artifacts']:
    assert '0.4.9' in name
    record = verification['artifacts'][name]
    assert sha256((root / 'dist' / name).read_bytes()) == record['sha256']
    artifacts[name] = record
smoke_log = root / next(step for step in verification['steps'] if step['name'] == 'wheel-smoke')['log']
records = [json.loads(line) for line in smoke_log.read_text(encoding='utf-8').splitlines() if line.startswith('{"status": "passed"')]
assert len(records) == 1
smoke = records[0]
assert smoke['version'] == __version__
assert Path(smoke['escape_results']['marker_module']).is_relative_to(smoke_log.parent / 'wheel-env')
test_xml = smoke_log.parent / 'pytest.xml'
cases = list(ET.parse(test_xml).getroot().iter('testcase'))
escape_cases = [case for case in cases if 'test_marker_escapes' in case.get('classname', '')]
assert len(escape_cases) == 64
focused_path = root / '.test-tmp/escape-focused-pass.xml'
focused = ET.parse(focused_path).getroot().find('testsuite')
assert focused is not None and focused.get('tests') == '103' and focused.get('failures') == focused.get('errors') == '0'

def hashes(directory):
    return {path.relative_to(directory).as_posix(): sha256(path.read_bytes()) for path in sorted(directory.rglob('*')) if path.is_file() and '__pycache__' not in path.parts and '.key' not in path.name}

escapes = smoke['escape_results']['workflows']
escape_hashes = {}
for mode, item in escapes.items():
    selected = Path(item['root'])
    assert item['strict_bytes_preserved'] and item['encrypted_edited_bytes_preserved']
    assert item['neutral_execution']['split_cases'] == 13530
    assert item['restored_execution']['split_cases'] == 1953
    assert item['neutral_execution']['invalid_streams'] == item['restored_execution']['invalid_streams'] == 7
    assert sha256((selected / 'parser.py').read_bytes()) == item['restored_sha256']
    assert (selected / 'parser.py').read_bytes().startswith(b'\xef\xbb\xbf')
    assert (selected / 'opened.py').read_bytes() == (selected / 'view/parser.py').read_bytes()
    assert (selected / 'decrypted_ready.py').read_bytes() == (selected / 'parser.py').read_bytes()
    workspace = Workspace(selected)
    assert view_status(workspace, item['export']['view_id'])['status'] == 'complete'
    _, manifest, _, _, _ = load_view(workspace, item['export']['view_id'])
    assert manifest_marker_version(manifest) == 2 and manifest['schema_version'] == 2
    assert all(row['marker_version'] == 2 for row in item['review']['return_validation']['files'])
    ledger = json.loads((selected / '.parser_safe.py.scm_ledger.json').read_bytes())
    assert ledger['schema_version'] == 3 and service.ledger_marker_version(ledger) == 2
    if mode == 'mcp':
        agent = item['source_agent']
        assert agent['tools'] == ['read_safe_file', 'write_safe_file']
        assert agent['read_marker_version'] == 2 and agent['write']['receipt_saved']
        assert agent['execution']['split_cases'] == 1953
        assert sha256((Path(agent['root']) / agent['write']['history'] / 'original.bin').read_bytes()) == item['original_sha256']
    escape_hashes[mode] = hashes(selected)

assert smoke['lexicon_results']['neutral_and_restored_statistics_pipeline_executed']
assert smoke['document_format_results']['strict_original_bytes_restored']
assert smoke['document_format_results']['bamb5_separate_authenticated_roundtrip']
assert smoke['document_format_results']['encryption_composition']['decoded_original_bytes_restored']
for item in smoke['fixture_results'].values():
    assert item['neutral_and_original_catalog_executed'] and item['apply']['receipt_saved']
for item in smoke['project_view_results'].values():
    assert item['encoded_and_restored_dependent_programs_executed']
for item in smoke['return_results'].values():
    assert item['encrypted_edited_bytes_preserved'] and item['neutral_and_restored_programs_executed']
assert len(Dictionary.load()) == 415 and len(Dictionary.load(profile='profanity')) == 310
context_function = runpy.run_path(str(root / 'scripts/vandal.py'))['context']
contexts = {mode: context_function(mode) for mode in ('agent', 'operator')}
for context in contexts.values():
    assert context['product_version'] == __version__ and context['workflow_version'] == '0.2'
    assert context['workflow'] == 'vigiles-vandal_0.2' and context['marker_version'] == 2
    assert context['specification_sha256'] == sha256((root / '.mivim/vigiles-vandal_0.2.md').read_bytes())
    assert context['skill_sha256'] == sha256((root / '.vigiles/skills/vigiles-vandal/SKILL.md').read_bytes())

destination = report_destination(root, 'VIGILES_TRANSLATION_QUALIFICATION.json')
prior = json.loads(destination.read_bytes())
assert prior['release'] == '0.4.8'
previous_verifications = []
for path in (root / '.mivim/vigiles/reports/archive/runs').glob('*/VIGILES_VERIFICATION.json'):
    record = json.loads(path.read_bytes())
    if record.get('source_sha256') == prior['source_sha256'] and record.get('status') == 'passed':
        previous_verifications.append(path)
assert previous_verifications
historical = {'views': {}, 'ledgers': {}}
for kind in ('installed_fixture_workflow', 'installed_return_validation_workflow'):
    for mode, item in prior[kind].items():
        selected = Path(item['root'])
        workspace = Workspace(selected)
        before = hashes(selected)
        view_id = item['export']['view_id'] if kind == 'installed_fixture_workflow' else item['operator']['review']['view_id']
        status = view_status(workspace, view_id)
        assert status['status'] == 'complete'
        _, manifest, _, _, _ = load_view(workspace, view_id)
        assert manifest['schema_version'] == 1 and manifest_marker_version(manifest) == 1
        assert hashes(selected) == before
        historical['views'][f'{kind}:{mode}'] = {'root': str(selected), 'status': status, 'manifest_sha256': sha256((selected / '.local/bamboozle/views' / view_id / 'manifest.json').read_bytes()), 'unchanged': True}
        if kind == 'installed_return_validation_workflow':
            ledger = workspace.read('.source_safe.py.scm_ledger.json')
            assert json.loads(ledger)['schema_version'] == 1
            assert service._restore(workspace.read('baseline.py'), ledger) == workspace.read('source.py')
            validation = validate_return(workspace, 'baseline.py', 'source.py', ledger='.source_safe.py.scm_ledger.json')
            assert validation['status'] == 'accepted' and validation['marker_version'] == 1
            historical['ledgers'][mode] = {'schema_version': 1, 'marker_version': 1, 'strict_original_bytes_restored': True, 'baseline_validation': validation}
selected = Path(prior['installed_document_format_workflow']['root'])
workspace = Workspace(selected)
old_view_id = prior['installed_document_format_workflow']['operator_mcp']['export']['view_id']
directory, manifest, _, _, _ = load_view(workspace, old_view_id)
for name in ('template', 'guide'):
    suffix = '.json' if name == 'template' else '.md'
    entry = manifest['files'][name + suffix]
    original = blob(workspace, directory, entry['original_sha256'])
    ledger = (selected / ('.' + name + '_safe' + suffix + '.scm_ledger.json')).read_bytes()
    mapping, record = service._ledger_dictionary(ledger)
    assert record['schema_version'] == 2 and service.ledger_marker_version(record) == 1
    rules, context = service.ledger_format(record)
    encoded = DocumentCodec(mapping, rules, marker_version=1).transform(original.decode('utf-8')).text.encode('utf-8')
    assert encoded == blob(workspace, directory, entry['view_sha256'])
    assert sha256(encoded) == record['encoded_sha256']
    assert service._restore(encoded, ledger) == original
    historical['ledgers'][name] = {'schema_version': 2, 'marker_version': 1, 'encoded_sha256': sha256(encoded), 'ledger_sha256': sha256(ledger), 'strict_original_bytes_restored': True}

try:
    FormatRules('python')
except ValueError as exc:
    next_reproduction = {'status': 'unmet_design_requirement', 'observed': str(exc), 'attempted_rules': {'format': 'python'}, 'design_source': '.legacy/bamboozle_v0.0.1.md:303-317', 'scope': 'No explicit syntax-aware source identifier adapter; source-agent code still uses intentionally selected whole-term text mode.'}
else:
    raise AssertionError('Reassess next gate: Python adapter already exists')

review = json.loads((root / '.test-tmp/escape-review-pointer.json').read_bytes())['review']
directory = root / '.mivim/vigiles/reports/archive/runs' / (datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ') + '-escape-compatibility')
directory.mkdir()
retained = {}
for name in ('.test-tmp/escape-product.xml', '.test-tmp/escape-focused-final.xml', '.test-tmp/escape-focused-pass.xml', '.test-tmp/escape-scenario-first/execution.log', '.test-tmp/escape-scenario-first/scenario.log'):
    raw = (root / name).read_bytes()
    target = directory / Path(name).name
    target.write_bytes(raw)
    retained[target.name] = sha256(raw)
write_report(root, directory / 'REVIEW_FINDINGS.json', {'schema_version': 1, 'created': utc_now(), 'stage': 'VIG-CIPHER-ESCAPE-01', 'design_review': review, 'files_sha256': retained, 'retained_findings': ['First product run: 605 passed, 7 failed due to former forward rejection contract. Reverse rejection remains enforced; tests now also verify lossless original syntax and explicit legacy rejection.', 'First focused adaptation: 96 passed, 7 failed due to a test using .text on the string convenience API. Corrected test API usage; focused final 103 passed.', 'First scenario launcher had a wrong relative script path; preserved log and corrected invocation. Complete CLI/operator MCP/source-agent scenario then passed.', 'Marker version is pinned separately from mapping/document context. Real 0.4.8 ledgers and completed fixture/return views reconstruct without mutation.'], 'historical_compatibility': historical})
scope = 'Local Windows Python 3.13 source and isolated installed v0.4.9 wheel. Versioned single-pass reserved-prefix escaping through original-source CLI, operator/source-agent MCP, structured regions, normal/strict/edited/new returns, restoration and recoverable views. Executed multi-module incremental stream parser covers all three-chunk splits, invalid frames, bounded payloads and reset after failure in neutral/restored programs. Explicit encryption/decryption preserves edited escaped source before semantic return. Original 0.4.8 saved ledgers and completed views remain readable with protocol 1. Deterministic agent edits do not prove external model behavior, remote CI, client registration, host isolation or production acceptance.'
report = {
    'schema_version': 1, 'created': utc_now(), 'release': __version__, 'stage': 'VIG-CIPHER-ESCAPE-01', 'status': 'complete_source_and_local_installed_package', 'scope': scope,
    'tests': verification['tests'], 'verification_stages_passed': 11, 'verification': verification_path.relative_to(root).as_posix(), 'source_sha256': verification['source_sha256'], 'artifacts': artifacts,
    'marker_protocol': {'current': 2, 'legacy': 1, 'new_ledger_schema': 3, 'new_view_manifest_schema': 2, 'payload': 'one exact reserved prefix in canonical lowercase UTF-8 hex', 'recursive_decoding': False, 'dictionary_namespace_changed': False},
    'installed_escape_workflow': escapes, 'installed_escape_file_sha256': escape_hashes, 'installed_marker_module': smoke['escape_results']['marker_module'],
    'historical_0_4_8_compatibility': historical,
    'installed_return_validation_workflow': smoke['return_results'], 'installed_fixture_workflow': smoke['fixture_results'], 'installed_lexicon_workflow': smoke['lexicon_results'],
    'installed_document_format_workflow': smoke['document_format_results'], 'installed_project_view_workflow': smoke['project_view_results'], 'installed_profanity_workflow': smoke['profanity_results'],
    'retained_interfaces': {key: smoke[key] for key in ('translation_results', 'agent_cipher_results', 'diagnostic_results', 'plan_results', 'capture_results')},
    'repository_workflow_contexts': contexts, 'product_workflow_boundary': {'product': __version__, 'workflow': 'vigiles-vandal_0.2', 'workflow_version': '0.2', 'source_agent_tools': ['read_safe_file', 'write_safe_file'], 'client_registered': False},
    'original_mappings_preserved': 110, 'default_dictionary_entries': 415, 'profanity_dictionary_entries': 310,
    'retained_review_failures': [review, (directory / 'REVIEW_FINDINGS.json').relative_to(root).as_posix()], 'focused_tests': {'tests': 103, 'failures': 0, 'path': focused_path.relative_to(root).as_posix(), 'sha256': sha256(focused_path.read_bytes())},
    'next_core_reproduction': next_reproduction,
}
previous = write_report(root, destination, report, replace=True)
assert previous is not None
status_path = root / 'VIGILES_STATUS.json'
status = json.loads(status_path.read_bytes())
completed = dict(status['next_step'])
assert completed['id'] == 'VIG-CIPHER-ESCAPE-01'
completed.update(status='complete_source_and_local_installed_package', evidence=[destination.relative_to(root).as_posix(), verification_path.relative_to(root).as_posix()], qualification={'tests_passed':842,'verification_stages_passed':11,'scope':scope}, retained_review_failures=report['retained_review_failures'], remaining='Syntax-aware source identifier translation and independent acceptance gates remain open.')
next_step = {'id': 'VIG-CIPHER-SOURCE-01', 'status': 'next', 'focus': 'Explicit syntax-aware Python source translation with protected strings/comments and consistent selected identifiers', 'design_source': ['.legacy/bamboozle_v0.0.1.md:303-317'], 'remaining': 'Current text mode translates whole terms in identifiers, strings and comments together. Add an explicit Python adapter with declared identifier scope and collision diagnostics; preserve supported whole-term mode and avoid inferring arbitrary runtime string semantics.', 'done_criteria': ['Define an explicit source-symbol and import/reference contract with token-preserving edits, scope/collision checks and clear handling of dynamic references', 'Preserve unselected strings/comments, formatting, BOM/newlines and literal marker syntax; expose deliberate separate translation of selected text content', 'Carry adapter context through saved ledgers, structured views, return validation, CLI and operator MCP without broadening source-agent tool authority', 'Execute realistic dependent modules before/after neutral edits and restored returns, including imports, shadowed/builtin names, keyword arguments and refused collisions', 'Retain historical formats/markers/contexts and qualify source plus isolated installed-package workflows']}
status['last_completed_step'] = completed
status['next_step'] = next_step
status['core_development_queue'] = [next_step, *[item for item in status['core_development_queue'] if item['id'] != completed['id']]]
status['release'] = __version__
status['recorded_on'] = '2026-10-04'
status['evidence'].update(prior_translation_qualification=previous.relative_to(root).as_posix(), prior_release_package_verification=previous_verifications[-1].relative_to(root).as_posix(), qualification_scope=scope)
write_report(root, status_path, status, replace=True)
print(json.dumps({'status':'qualified','release':__version__,'tests':verification['tests'],'stages':11,'source_hashes':len(verification['source_sha256']),'qualification':destination.relative_to(root).as_posix(),'next_step':next_step['id']}))
