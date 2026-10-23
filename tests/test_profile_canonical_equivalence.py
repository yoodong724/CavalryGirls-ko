"""Release profile guards when canonical text survives a new distribution build."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from tools.maintenance import profile
from tools.maintenance import package

CANON = '8c8c7a868d776f158e2d7f0f2c494458676cac3607ea9308bb929f809e02de6c'
DIST = 'd8734013c03e2340ad6472e5c73d614c67f850afa13896cf2e495d4b6741d5bc'
SOURCE_HASHES = Path(__file__).resolve().parents[1] / 'release/reference/build26154816/source-hashes.json'


class EquivalenceProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bindings = {}
        for name in ('corpus.jsonl', 'maps.jsonl', 'snapshot.jsonl', 'reviews.jsonl',
                     'asset-profile.json', 'canonical-source-manifest.json'):
            self.bindings[name] = self.write(name, b'fixture-' + name.encode())
        self.manifest = json.loads(SOURCE_HASHES.read_text())
        self.required_hashes = {row['path']: row['sha256'] for row in self.manifest['files']
            if row['path'] in profile.REQUIRED_SOURCE_PATHS}
        self.raw = {
            'game_version': '3.1.3048', 'source_revision': CANON,
            'distribution_source_revision': DIST, 'patch_revision': 'r14',
            'steam_build_id': '26154816', 'record_count': 10349,
            'text_target_ids': [1], 'title': {'offset': 0, 'size': 1},
            'coverage': {'qa': True, 'l1': True, 'l2': True},
            'bound_source_hashes': self.required_hashes,
            'digests': {
                'corpus': self.bindings['corpus.jsonl'], 'maps': self.bindings['maps.jsonl'],
                'snapshot': self.bindings['snapshot.jsonl'], 'reviews': self.bindings['reviews.jsonl'],
                'profile': self.bindings['asset-profile.json'],
            },
            'canonical_source_manifest': self.bindings['canonical-source-manifest.json'],
        }
        self.refresh_manifest()
        self.evidence = {
            'canonical_revision': CANON, 'distribution_revision': DIST,
            'steam_build_id': '26154816', 'record_count': 10349,
            'table_record_count': 10334, 'static_ui_record_count': 14, 'hud_record_count': 1,
            'localized_objects_identical': True,
            'canonical_localized_facts_sha256': 'c' * 64,
            'distribution_localized_facts_sha256': 'c' * 64,
            'corpus_sha256': self.bindings['corpus.jsonl']['sha256'],
            'map_sha256': self.bindings['maps.jsonl']['sha256'],
            'canonical_source_manifest_sha256': self.bindings['canonical-source-manifest.json']['sha256'],
            'distribution_source_hash_manifest_sha256': self.raw['source_hash_manifest']['sha256'],
        }
        self.refresh_evidence()

    def write(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return {'path': name, 'sha256': profile.sha256_file(path)}

    def refresh_manifest(self):
        self.raw['source_hash_manifest'] = self.write('source-hashes.json', json.dumps(self.manifest).encode())

    def refresh_evidence(self):
        self.raw['canonical_equivalence'] = self.write('equivalence.json', json.dumps(self.evidence).encode())

    def load(self):
        path = self.root / 'build-profile.json'
        path.write_text(json.dumps(self.raw))
        return profile.load_profile(path, root=self.root)

    def test_new_build_accepts_separate_revisions_and_bound_evidence(self):
        loaded = self.load()
        self.assertEqual(loaded.source_revision, CANON)
        self.assertEqual(loaded.distribution_source_revision, DIST)
        self.assertEqual(loaded.patch_revision, 'r14')

    def test_new_build_rejects_missing_and_wrong_identity(self):
        for key, bad in (('game_version', '3.1.3040'), ('steam_build_id', '26150116'),
                         ('source_revision', DIST), ('distribution_source_revision', CANON)):
            with self.subTest(key=key):
                old = self.raw[key]
                self.raw[key] = bad
                with self.assertRaises(profile.ProfileError): self.load()
                self.raw[key] = old
        for key in ('distribution_source_revision', 'canonical_equivalence'):
            with self.subTest(missing=key):
                old = self.raw.pop(key)
                with self.assertRaises(profile.ProfileError): self.load()
                self.raw[key] = old

    def test_new_build_rejects_false_distribution_manifest(self):
        for key, bad in (('source_revision', CANON), ('game_version', '3.1.3040'),
                         ('steam_build_id', '26150116')):
            with self.subTest(key=key):
                old = self.manifest[key]
                self.manifest[key] = bad
                self.refresh_manifest()
                with self.assertRaises(profile.ProfileError): self.load()
                self.manifest[key] = old
                self.refresh_manifest()
        self.manifest['files'][0]['sha256'] = 'b' * 64
        self.refresh_manifest()
        with self.assertRaises(profile.ProfileError): self.load()

    def test_new_build_rejects_modified_unlocalized_distribution_file(self):
        row = next(row for row in self.manifest['files'] if row['path'] not in profile.REQUIRED_SOURCE_PATHS)
        row['sha256'] = 'b' * 64
        self.refresh_manifest()
        self.evidence['distribution_source_hash_manifest_sha256'] = self.raw['source_hash_manifest']['sha256']
        self.refresh_evidence()
        with self.assertRaises(profile.ProfileError): self.load()

    def test_new_build_rejects_stale_or_false_equivalence(self):
        for key, bad in (('canonical_revision', DIST), ('distribution_revision', CANON),
                         ('steam_build_id', '26150116'), ('record_count', 10348),
                         ('localized_objects_identical', False), ('corpus_sha256', 'b' * 64),
                         ('distribution_localized_facts_sha256', 'd' * 64),
                         ('map_sha256', 'b' * 64), ('canonical_source_manifest_sha256', 'b' * 64),
                         ('distribution_source_hash_manifest_sha256', 'b' * 64)):
            with self.subTest(key=key):
                old = self.evidence[key]
                self.evidence[key] = bad
                self.refresh_evidence()
                with self.assertRaises(profile.ProfileError): self.load()
                self.evidence[key] = old
        self.refresh_evidence()
        (self.root / 'maps.jsonl').write_bytes(b'mutated')
        with self.assertRaises(profile.ProfileError): self.load()

    def test_legacy_r13_r14_registry_kept(self):
        self.assertEqual(profile.SUPPORTED_PROFILE_RELEASES['3.1.3032']['patch_revision'], 'r13')
        self.assertEqual(profile.SUPPORTED_PROFILE_RELEASES['3.1.3040']['steam_build_id'], '26150116')

    def test_mutated_pristine_build_input_is_rejected(self):
        source_hashes = {}
        for relative in profile.REQUIRED_SOURCE_PATHS:
            path = self.root / 'game' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'original')
            source_hashes[relative] = profile.sha256_file(path)
        spec = SimpleNamespace(source_hashes=source_hashes)
        profile.validate_source_game(spec, self.root / 'game')
        (self.root / 'game' / profile.REQUIRED_SOURCE_PATHS[0]).write_bytes(b'mutated')
        with self.assertRaises(profile.ProfileError):
            profile.validate_source_game(spec, self.root / 'game')

    def test_package_requires_matching_distribution_revision_in_build_manifest(self):
        build = self.root / 'build'
        game = self.root / 'game'
        build.mkdir()
        (game / 'CavalryGirls_Data').mkdir(parents=True)
        source_hashes = {}
        outputs = {}
        for name in ('resources.assets', 'resources.assets.resS'):
            source = game / 'CavalryGirls_Data' / name
            built = build / name
            source.write_bytes(('source-' + name).encode())
            built.write_bytes(('built-' + name).encode())
            source_hashes['CavalryGirls_Data/' + name] = profile.sha256_file(source)
            outputs[name] = {'sha256': profile.sha256_file(built), 'size': built.stat().st_size}
        artifacts = {key: {'sha256': 'a' * 64} for key in ('corpus', 'maps', 'snapshot', 'reviews', 'profile')}
        spec = SimpleNamespace(version='3.1.3048', patch_revision='r14', source_revision=CANON,
            distribution_source_revision=DIST, record_count=3, text_target_ids=(1,),
            path=self.root / 'build-profile.json', source_manifest_sha256='b' * 64,
            source_hashes=source_hashes, coverage={'qa': True}, artifacts=artifacts,
            raw={'supplemental_id': 'hud'})
        (spec.path).write_text('{}')
        dependencies = {'dep': 'c' * 64}
        manifest = {
            'source_revision': CANON, 'distribution_source_revision': DIST,
            'runtime_verified': False, 'release_scope': 'stage8_local_candidate',
            'source_protected_read_only': True, 'all_object_payloads_verified': True,
            'patch_revision': 'r14', 'total_localized_record_count': 3,
            'translation_snapshot': {'record_count': 3, 'base_record_count': 2,
                'supplemental_record_count': 1, 'static_ui_record_count': 0},
            'profile': {'version': spec.version, 'source_revision': CANON,
                'distribution_source_revision': DIST, 'sha256': profile.sha256_file(spec.path),
                'source_hash_manifest_sha256': spec.source_manifest_sha256,
                'coverage': spec.coverage, 'artifact_digests': {key: value['sha256'] for key, value in artifacts.items()},
                'dependency_hashes': dependencies},
            'text_records': [{'path_id': 1, 'requested_cell_count': 2}],
            'fonts': {'validation': {'local_pptr_resolved': True}},
            'external_title': {'outside_span_equal': True}, 'supplemental_ui': {'id': 'hud'},
            'outputs': outputs,
        }
        old_work = package.WORK_ROOT
        package.WORK_ROOT = self.root
        self.addCleanup(setattr, package, 'WORK_ROOT', old_work)
        path = build / 'build-manifest.json'
        path.write_text(json.dumps(manifest))
        package.validate_build(build, game, profile=spec,
            profile_dependency_hashes=dependencies, profile_paths_map={})
        for value in (None, CANON):
            with self.subTest(distribution=value):
                changed = dict(manifest)
                if value is None: changed.pop('distribution_source_revision')
                else: changed['distribution_source_revision'] = value
                path.write_text(json.dumps(changed))
                with self.assertRaises(package.PackageError):
                    package.validate_build(build, game, profile=spec,
                        profile_dependency_hashes=dependencies, profile_paths_map={})


if __name__ == '__main__': unittest.main()
