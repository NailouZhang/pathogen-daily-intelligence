from pathlib import Path

from src.pdi.pipeline import run_daily_pipeline


def test_demo_is_chinese_first_with_per_card_english_toggle_and_real_subscript(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / 'out'
    result = run_daily_pipeline(root, 'hantavirus', out, demo_mode=True, disable_llm=True)

    assert all(work.get('title', {}).get('translated_zh') for work in result['works'])
    assert all(article.get('title', {}).get('translated_zh') for article in result['articles'])
    assert all(event.get('summary_zh') for event in result['events'])

    html = (out / 'site/index.html').read_text(encoding='utf-8')
    assert 'class="language-toggle"' in html
    assert '显示英文' in html
    assert 'G<sub>N</sub>/G<sub>C</sub>' in html
    assert 'G&lt;sub&gt;N&lt;/sub&gt;' not in html
    assert '正汉坦病毒 G<sub>N</sub>/G<sub>C</sub> 刺突蛋白' in html
    assert 'data-lang="en" hidden' in html
