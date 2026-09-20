"""extractors/jats_extractor — Europe PMC JATS XML → pipeline text."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from extractors.jats_extractor import jats_to_text

_XML = """<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
 <front><article-meta>
   <title-group><article-title>Step and Exponential Taper in Strength Athletes</article-title></title-group>
   <abstract><p>A taper is often prescribed <xref ref-type="bibr" rid="B1">1</xref>, <xref rid="B2">2</xref>.</p></abstract>
 </article-meta></front>
 <body>
  <sec id="s1"><title>Introduction</title>
   <p>Tapers reduce volume by 41–60% [<xref rid="B3">3</xref>] while intensity is held.</p>
   <sec id="s1a"><title>Design</title><p>Sixteen powerlifters were matched.</p>
     <list><list-item><p>step taper</p></list-item><list-item><p>exponential taper</p></list-item></list>
   </sec>
  </sec>
  <sec id="s2"><title>Training Program</title>
   <table-wrap id="T1"><label>Table 1</label><caption><p>Weekly loading.</p></caption>
     <table><tr><th>Week</th><th>Sets x reps</th></tr><tr><td>1</td><td>3 x 5 @ 80%</td></tr></table>
     <table-wrap-foot><p>Footnote to drop</p></table-wrap-foot>
   </table-wrap>
   <fig id="F1"><caption><p>Figure 1. Study timeline.</p></caption><graphic xlink:href="f1.jpg"/></fig>
  </sec>
 </body>
 <back><ref-list><ref id="B1"><mixed-citation>Reference One</mixed-citation></ref></ref-list></back>
</article>"""


def test_jats_to_text_renders_headings_paragraphs_tables_and_drops_references():
    text = jats_to_text(_XML)
    assert text.startswith("# Step and Exponential Taper in Strength Athletes\n\n## Abstract\n\nA taper is often prescribed.\n\n")
    assert "## Introduction\n\nTapers reduce volume by 41–60% while intensity is held.\n\n### Design" in text
    assert "- step taper\n\n- exponential taper" in text
    assert "Table 1 Weekly loading.\n\nWeek\tSets x reps\n\n1\t3 x 5 @ 80%" in text
    assert "Figure 1. Study timeline." in text
    for dropped in ("Reference One", "Footnote to drop", "[]", "[,]", "f1.jpg"):
        assert dropped not in text, dropped
    assert text.endswith("\n") and "\n\n\n" not in text
