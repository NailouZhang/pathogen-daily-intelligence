from src.pdi.markup import (
    protect_scientific_markup,
    restore_scientific_markup,
    safe_scientific_html,
)


def test_safe_scientific_markup_preserves_subscript_and_blocks_unsafe_html():
    value = 'G<sub>N</sub> <script>alert(1)</script> <img src=x onerror=alert(2)>X'
    rendered = safe_scientific_html(value)
    assert 'G<sub>N</sub>' in rendered
    assert '<script>' not in rendered
    assert '<img' not in rendered
    assert 'alert(1)' in rendered  # inert text remains visible rather than executable


def test_protected_markup_round_trip():
    protected, mapping = protect_scientific_markup('G<sub>N</sub>/G<sub>C</sub>')
    assert '[[PDI_SCI_000]]' in protected
    assert restore_scientific_markup(protected, mapping) == 'G<sub>N</sub>/G<sub>C</sub>'
