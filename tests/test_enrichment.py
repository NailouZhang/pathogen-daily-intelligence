from src.pdi.enrichment import _extract_main_text


def test_jsonld_article_body_is_preferred():
    html = '''
    <html><head><script type="application/ld+json">
    {"@type":"NewsArticle","headline":"Notice","articleBody":"'''+("Official laboratory surveillance reported a new finding. "*12)+'''"}
    </script></head><body><p>navigation only</p></body></html>
    '''
    text, method, metadata = _extract_main_text(html)
    assert method == "jsonld_articleBody"
    assert text and "laboratory surveillance" in text
    assert metadata["headline"] == "Notice"


def test_article_container_extracts_paragraphs():
    paragraph = "The health authority released a detailed public health update with laboratory and response information. "
    html = f"<html><body><article><p>{paragraph * 4}</p><p>{paragraph * 4}</p></article></body></html>"
    text, method, _ = _extract_main_text(html)
    assert text and len(text) > 250
    assert method.startswith("css:")
