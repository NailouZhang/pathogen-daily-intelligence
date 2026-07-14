from src.pdi.translation import (
    apply_translation,
    prepare_translation_item,
    restore_translation_fields,
    validate_translation_fields,
)


def test_translation_validation_preserves_numbers_and_scientific_placeholders():
    work = {
        'work_id': 'work-1',
        'title': {'original': 'G<sub>N</sub> binding in 2 cases', 'language': 'en'},
        'abstract': {'original': 'The study included 2 cases and reported 50%.', 'sentences': []},
    }
    prepared, mapping = prepare_translation_item(work, 'work')
    fields = {
        'translated_title_zh': prepared['title'].replace('binding in', '结合，涉及'),
        'translated_text_zh': prepared['text'].replace('The study included', '研究纳入').replace('cases and reported', '例，并报告'),
        'display_summary_zh': '研究报告 2 例。',
        'display_summary_en': 'The study included 2 cases.',
    }
    result = validate_translation_fields(
        work['title']['original'], work['abstract']['original'], fields, mapping
    )
    assert result['valid'], result['errors']
    restored = restore_translation_fields(fields, mapping)
    apply_translation(work, 'work', restored, {'provider': 'test', 'validation_status': 'passed'})
    assert '<sub>N</sub>' in work['title']['translated_zh']
    assert work['display_summary']['zh']


def test_translation_validation_rejects_dropped_number():
    fields = {
        'translated_title_zh': '病例报告',
        'translated_text_zh': '病例报告。',
        'display_summary_zh': '病例报告。',
        'display_summary_en': 'Case report.',
    }
    result = validate_translation_fields('Report of 2 cases', 'There were 2 cases.', fields, {})
    assert not result['valid']
    assert any('2' in error for error in result['errors'])


def test_batch_translation_path_applies_validated_output():
    from src.pdi.pipeline import _translate_remaining_items

    class FakeRun:
        output = None

        def __init__(self, output):
            self.output = output

        def audit(self):
            return {
                'provider': 'fake',
                'model': 'fake-model',
                'status': 'success',
                'retry_count': 0,
                'fallback_used': False,
                'input_hash': 'fake',
                'generated_at': '2026-07-14T00:00:00+00:00',
            }

    class FakeRouter:
        def run(self, task_name, payload):
            assert task_name == 'bilingual_translation_batch'
            rows = []
            for item in payload['items']:
                rows.append({
                    'record_id': item['record_id'],
                    'translated_title_zh': item['title'].replace('Report of', '关于').replace('cases', '例病例的报告'),
                    'translated_text_zh': item['text'].replace('The report included', '该报告纳入').replace('cases.', '例病例。'),
                    'display_summary_zh': '报告涉及 2 例病例。',
                    'display_summary_en': 'The report included 2 cases.',
                    'uncertainties': [],
                })
            return FakeRun({'items': rows})

    work = {
        'work_id': 'work-batch',
        'title': {'original': 'Report of G<sub>N</sub> in 2 cases', 'translated_zh': None, 'language': 'en'},
        'abstract': {'original': 'The report included 2 cases.', 'translated_zh': None, 'sentences': []},
        'display_summary': {'zh': None, 'en': None},
    }
    audits = []
    cache = {}
    _translate_remaining_items(FakeRouter(), [(work, 'work')], cache, audits, 6)
    assert work['title']['translated_zh']
    assert '<sub>N</sub>' in work['title']['translated_zh']
    assert work['display_summary']['zh'] == '报告涉及 2 例病例。'
    assert cache
    assert audits[0]['validation_status'] == 'passed'
