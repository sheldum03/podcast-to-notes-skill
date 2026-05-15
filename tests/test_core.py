"""
Core unit tests for podcast-to-notes-skill.

Covers pure functions in prepare.py and render.py that have no
external dependencies (no network, no disk I/O, no LLM calls).
"""

import sys
from pathlib import Path

# Allow imports from the scripts directory.
SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import pytest
from unittest.mock import patch
from prepare import (estimate_tokens, _cjk_ratio, chunk_segments, format_segment,
                     fmt_timestamp, validate_audio_file, compute_section_range,
                     merge_chunk_outlines, _title_word_overlap,
                     PASS2_FULL_THRESHOLD, PASS2_CHUNKED_THRESHOLD)
from render import (validate_outline, validate_inputs, validate_timestamps,
                    ts_to_seconds, fmt_date, detect_language,
                    _md_to_html_with_timestamps, _parse_insights_sections)


# ==================== estimate_tokens ====================

class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_english_text(self):
        text = "This is a simple English sentence for testing."
        tokens = estimate_tokens(text)
        # ~46 chars / 4 = ~11 tokens
        assert 8 <= tokens <= 15

    def test_chinese_text(self):
        text = "这是一段用于测试的中文文本内容"
        tokens = estimate_tokens(text)
        # 14 CJK chars / 1.2 = ~11 tokens
        assert 8 <= tokens <= 15

    def test_mixed_text(self):
        text = "这是mixed混合text测试"
        tokens = estimate_tokens(text)
        assert tokens > 0
        # Should be between pure-CJK and pure-English estimates
        pure_cjk_est = int(len(text) / 1.2)
        pure_latin_est = int(len(text) / 4)
        assert pure_latin_est <= tokens <= pure_cjk_est

    def test_returns_int(self):
        assert isinstance(estimate_tokens("hello world"), int)


# ==================== _cjk_ratio ====================

class TestCjkRatio:
    def test_empty_string(self):
        assert _cjk_ratio("") == 0.0

    def test_pure_latin(self):
        assert _cjk_ratio("Hello world") == 0.0

    def test_pure_cjk(self):
        ratio = _cjk_ratio("你好世界")
        assert ratio == 1.0

    def test_mixed(self):
        # "你好world" = 2 CJK + 5 Latin = 2/7
        ratio = _cjk_ratio("你好world")
        assert 0.2 < ratio < 0.4

    def test_japanese_hiragana(self):
        # Hiragana is counted as CJK
        ratio = _cjk_ratio("あいう")
        assert ratio == 1.0

    def test_korean(self):
        ratio = _cjk_ratio("한글")
        assert ratio == 1.0


# ==================== validate_outline ====================

class TestValidateOutline:
    """validate_outline calls sys.exit on failure; we catch SystemExit."""

    def _valid_outline(self):
        return {
            "tldr": "A summary",
            "outline": [
                {"section": "Intro", "key_points": ["point 1"], "timestamp": "00:00"},
            ],
            "mindmap": {"root": "Topic", "branches": []},
            "key_terms": [],
        }

    def test_valid_outline_passes(self):
        # Should not raise
        validate_outline(self._valid_outline(), "test.json")

    def test_missing_top_level_keys(self):
        outline = {"tldr": "ok"}  # missing outline, mindmap, key_terms
        with pytest.raises(SystemExit):
            validate_outline(outline, "test.json")

    def test_outline_not_a_list(self):
        outline = self._valid_outline()
        outline["outline"] = "not a list"
        with pytest.raises(SystemExit):
            validate_outline(outline, "test.json")

    def test_outline_item_missing_section(self):
        outline = self._valid_outline()
        outline["outline"] = [{"key_points": ["a"]}]  # no "section"
        with pytest.raises(SystemExit):
            validate_outline(outline, "test.json")

    def test_outline_item_missing_key_points(self):
        outline = self._valid_outline()
        outline["outline"] = [{"section": "Intro"}]  # no "key_points"
        with pytest.raises(SystemExit):
            validate_outline(outline, "test.json")

    def test_mindmap_string_ok(self):
        outline = self._valid_outline()
        outline["mindmap"] = "mindmap\n  root((Topic))"
        validate_outline(outline, "test.json")  # should not raise

    def test_mindmap_wrong_type(self):
        outline = self._valid_outline()
        outline["mindmap"] = 42
        with pytest.raises(SystemExit):
            validate_outline(outline, "test.json")

    def test_key_terms_wrong_type(self):
        outline = self._valid_outline()
        outline["key_terms"] = "not a list"
        with pytest.raises(SystemExit):
            validate_outline(outline, "test.json")


# ==================== chunk_segments ====================

class TestChunkSegments:
    def _make_segments(self, count, text_per_seg="word " * 50):
        """Create a list of fake segments."""
        return [
            {"start": i * 30.0, "end": (i + 1) * 30.0, "text": text_per_seg.strip()}
            for i in range(count)
        ]

    def test_short_transcript_single_chunk(self):
        segs = self._make_segments(5)
        chunks = chunk_segments(segs)
        assert len(chunks) == 1
        assert chunks[0]["index"] == 0
        assert chunks[0]["is_overlap_extension"] is False

    def test_long_transcript_multiple_chunks(self):
        # Each segment ~50 words * 5 chars = ~250 chars => ~62 tokens.
        # Need >60000 tokens => >960 segments to exceed threshold.
        segs = self._make_segments(1200)
        chunks = chunk_segments(segs)
        assert len(chunks) > 1
        # First chunk should not be overlap-extended
        assert chunks[0]["is_overlap_extension"] is False
        # Subsequent chunks should be
        for c in chunks[1:]:
            assert c["is_overlap_extension"] is True

    def test_chunk_has_required_fields(self):
        segs = self._make_segments(5)
        chunks = chunk_segments(segs)
        chunk = chunks[0]
        assert "index" in chunk
        assert "start_seconds" in chunk
        assert "end_seconds" in chunk
        assert "transcript" in chunk
        assert "estimated_tokens" in chunk


# ==================== ts_to_seconds (render.py) ====================

class TestTsToSeconds:
    def test_mm_ss(self):
        assert ts_to_seconds("05:30") == 330

    def test_hh_mm_ss(self):
        assert ts_to_seconds("01:05:30") == 3930

    def test_with_brackets(self):
        assert ts_to_seconds("[05:30]") == 330

    def test_empty(self):
        assert ts_to_seconds("") == 0

    def test_none(self):
        assert ts_to_seconds(None) == 0


# ==================== fmt_date (render.py) ====================

class TestFmtDate:
    def test_yyyymmdd(self):
        assert fmt_date("20240115") == "2024-01-15"

    def test_already_formatted(self):
        assert fmt_date("2024-01-15") == "2024-01-15"

    def test_empty(self):
        assert fmt_date("") == ""

    def test_none(self):
        assert fmt_date(None) == ""


# ==================== detect_language (render.py) ====================

class TestDetectLanguage:
    def test_english_markers(self):
        md = "## Quotes\nSome quote here\n## Contrarian Takes\n"
        assert detect_language(md) == "en"

    def test_chinese_default(self):
        md = "## 金句\n一些引用\n"
        assert detect_language(md) == "zh"

    def test_empty(self):
        assert detect_language("") == "zh"

    def test_none(self):
        assert detect_language(None) == "zh"


# ==================== fmt_timestamp (prepare.py) ====================

class TestFmtTimestamp:
    def test_seconds_only(self):
        assert fmt_timestamp(45) == "00:45"

    def test_minutes_and_seconds(self):
        assert fmt_timestamp(125) == "02:05"

    def test_with_hours(self):
        assert fmt_timestamp(3661) == "01:01:01"

    def test_zero(self):
        assert fmt_timestamp(0) == "00:00"


# ==================== P0: validate_inputs (render.py) ====================

class TestValidateInputs:
    """render.py must check that outline.json and insights.md exist before rendering."""

    def test_all_files_exist(self, tmp_path):
        prep = tmp_path / "prep.json"
        outline = tmp_path / "outline.json"
        insights = tmp_path / "insights.md"
        prep.write_text("{}")
        outline.write_text("{}")
        insights.write_text("# test")
        # Should not raise
        validate_inputs(str(prep), str(outline), str(insights))

    def test_missing_outline(self, tmp_path):
        prep = tmp_path / "prep.json"
        insights = tmp_path / "insights.md"
        prep.write_text("{}")
        insights.write_text("# test")
        with pytest.raises(SystemExit):
            validate_inputs(str(prep), str(tmp_path / "outline.json"), str(insights))

    def test_missing_insights(self, tmp_path):
        prep = tmp_path / "prep.json"
        outline = tmp_path / "outline.json"
        prep.write_text("{}")
        outline.write_text("{}")
        with pytest.raises(SystemExit):
            validate_inputs(str(prep), str(outline), str(tmp_path / "insights.md"))

    def test_missing_prep(self, tmp_path):
        outline = tmp_path / "outline.json"
        insights = tmp_path / "insights.md"
        outline.write_text("{}")
        insights.write_text("# test")
        with pytest.raises(SystemExit):
            validate_inputs(str(tmp_path / "prep.json"), str(outline), str(insights))

    def test_error_message_includes_filename(self, tmp_path, capsys):
        prep = tmp_path / "prep.json"
        prep.write_text("{}")
        outline_path = str(tmp_path / "outline.json")
        with pytest.raises(SystemExit):
            validate_inputs(str(prep), outline_path, str(tmp_path / "insights.md"))
        captured = capsys.readouterr()
        assert "outline.json" in captured.err


# ==================== P1a: validate_timestamps (render.py) ====================

class TestValidateTimestamps:
    """Timestamps in outline.json must not exceed audio duration."""

    def _outline_with_timestamps(self, *timestamps):
        return {
            "tldr": "summary",
            "outline": [
                {"section": f"Section {i+1}", "key_points": ["pt"],
                 "timestamp": ts}
                for i, ts in enumerate(timestamps)
            ],
            "mindmap": {"root": "Topic", "branches": []},
            "key_terms": [],
        }

    def test_valid_timestamps_no_warning(self, capsys):
        outline = self._outline_with_timestamps("05:00", "20:00", "40:00")
        warnings = validate_timestamps(outline, duration=2700)  # 45 min
        assert len(warnings) == 0

    def test_timestamp_exceeds_duration(self):
        outline = self._outline_with_timestamps("05:00", "01:30:00")  # 90 min
        warnings = validate_timestamps(outline, duration=2700)  # 45 min
        assert len(warnings) > 0
        assert "01:30:00" in warnings[0]

    def test_no_duration_skips_validation(self):
        outline = self._outline_with_timestamps("99:99:99")
        warnings = validate_timestamps(outline, duration=0)
        assert len(warnings) == 0

    def test_mindmap_timestamps_checked(self):
        outline = {
            "tldr": "summary",
            "outline": [],
            "mindmap": {
                "root": "Topic",
                "branches": [
                    {"label": "B1", "children": [
                        {"label": "child", "timestamp": "02:00:00"}
                    ]}
                ]
            },
            "key_terms": [],
        }
        warnings = validate_timestamps(outline, duration=1800)  # 30 min
        assert len(warnings) > 0

    def test_multiple_bad_timestamps(self):
        outline = self._outline_with_timestamps("50:00", "55:00")
        warnings = validate_timestamps(outline, duration=2400)  # 40 min
        assert len(warnings) == 2


# ==================== P1b: validate_audio_file (prepare.py) ====================

class TestValidateAudioFile:
    """Audio files must be verified as playable via ffprobe before transcription."""

    def test_valid_audio_returns_true(self, tmp_path):
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"\x00" * 2048)
        with patch("prepare.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"format": {"duration": "120.5"}}'
            assert validate_audio_file(audio) is True

    def test_invalid_audio_raises(self, tmp_path):
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"\x00" * 2048)
        with patch("prepare.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "Invalid data found"
            with pytest.raises(SystemExit):
                validate_audio_file(audio)

    def test_ffprobe_not_found_warns(self, tmp_path, capsys):
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"\x00" * 2048)
        with patch("prepare.subprocess.run", side_effect=FileNotFoundError):
            # Should not raise — graceful degradation when ffprobe missing
            result = validate_audio_file(audio)
            assert result is True  # proceed without validation
        captured = capsys.readouterr()
        assert "ffprobe" in captured.out.lower() or "ffprobe" in captured.err.lower()

    def test_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(SystemExit):
            validate_audio_file(tmp_path / "nonexistent.m4a")


# ==================== P2: --audio-url in render.py ====================

class TestAudioUrl:
    """--audio-url should override local audio path in HTML output."""

    def _make_inputs(self, tmp_path):
        prep = {
            "metadata": {
                "title": "Test Episode",
                "uploader": "Test",
                "upload_date": "20240101",
                "duration": 1800,
                "audio_path": "/tmp/audio.m4a",
                "audio_filename": "audio.m4a",
                "webpage_url": "https://example.com/ep1",
            },
            "url": "https://example.com/ep1",
        }
        outline = {
            "tldr": "A test summary",
            "outline": [
                {"section": "Intro", "key_points": ["point 1"], "timestamp": "00:00",
                 "worth_listening": True},
            ],
            "mindmap": {"root": "Topic", "branches": []},
            "key_terms": [],
        }
        insights = "## Quotes\n> Some quote\n"
        return prep, outline, insights

    def test_audio_url_used_in_html_simple(self):
        from render import render_html_simple
        prep, outline, insights = self._make_inputs(None)
        audio_url = "https://cdn.example.com/episode1.mp3"
        html = render_html_simple(prep["metadata"], outline, insights,
                                   audio_url, prep["metadata"]["webpage_url"])
        assert "https://cdn.example.com/episode1.mp3" in html

    def test_audio_url_used_in_html_dashboard(self):
        from render import render_html_dashboard
        prep, outline, insights = self._make_inputs(None)
        audio_url = "https://cdn.example.com/episode1.mp3"
        html = render_html_dashboard(prep["metadata"], outline, insights,
                                      audio_url, prep["metadata"]["webpage_url"])
        assert "https://cdn.example.com/episode1.mp3" in html


# ==================== Issue 4: merge_chunk_outlines ====================

class TestTitleWordOverlap:
    def test_identical_titles(self):
        assert _title_word_overlap("Intro to AI", "Intro to AI") == 1.0

    def test_no_overlap(self):
        assert _title_word_overlap("Alpha Beta", "Gamma Delta") == 0.0

    def test_partial_overlap(self):
        overlap = _title_word_overlap("Discuss AI safety", "Discuss AI alignment")
        # 2 common words ("discuss", "ai") out of 3 min-set = 0.667
        assert 0.6 < overlap < 0.7

    def test_empty_title(self):
        assert _title_word_overlap("", "hello") == 0.0
        assert _title_word_overlap("hello", "") == 0.0


class TestMergeChunkOutlines:
    def _chunk(self, tldr, sections, branches=None, terms=None):
        return {
            "tldr": tldr,
            "outline": sections,
            "mindmap": {
                "root": "Topic",
                "branches": branches or [
                    {"label": "Branch A", "children": [{"label": "child1", "timestamp": "01:00"}]}
                ],
            },
            "key_terms": terms or [],
        }

    def test_single_chunk_returns_as_is(self):
        c = self._chunk("Summary", [{"section": "Intro", "key_points": ["pt1"]}])
        result = merge_chunk_outlines([c])
        assert result is c

    def test_empty_list_raises(self):
        with pytest.raises(ValueError):
            merge_chunk_outlines([])

    def test_tldr_concatenated(self):
        c1 = self._chunk("Part 1 summary", [{"section": "S1", "key_points": ["p"]}])
        c2 = self._chunk("Part 2 summary", [{"section": "S2", "key_points": ["p"]}])
        result = merge_chunk_outlines([c1, c2])
        assert "Part 1 summary" in result["tldr"]
        assert "Part 2 summary" in result["tldr"]
        assert " | " in result["tldr"]

    def test_outline_sections_concatenated(self):
        c1 = self._chunk("T1", [{"section": "Intro", "key_points": ["p1"]}])
        c2 = self._chunk("T2", [{"section": "Deep Dive", "key_points": ["p2"]}])
        result = merge_chunk_outlines([c1, c2])
        assert len(result["outline"]) == 2
        assert result["outline"][0]["section"] == "Intro"
        assert result["outline"][1]["section"] == "Deep Dive"

    def test_outline_dedup_similar_consecutive_sections(self):
        c1 = self._chunk("T1", [
            {"section": "Discuss AI safety measures", "key_points": ["p1", "p2", "p3"]},
        ])
        c2 = self._chunk("T2", [
            {"section": "Discuss AI safety measures", "key_points": ["p4"]},
        ])
        result = merge_chunk_outlines([c1, c2])
        # Should keep the one with more key_points (c1's version)
        assert len(result["outline"]) == 1
        assert len(result["outline"][0]["key_points"]) == 3

    def test_mindmap_branches_merged_by_label(self):
        c1 = self._chunk("T1", [{"section": "S1", "key_points": ["p"]}],
                          branches=[{"label": "AI", "children": [{"label": "GPT", "timestamp": "01:00"}]}])
        c2 = self._chunk("T2", [{"section": "S2", "key_points": ["p"]}],
                          branches=[{"label": "AI", "children": [{"label": "Claude", "timestamp": "02:00"}]}])
        result = merge_chunk_outlines([c1, c2])
        ai_branch = [b for b in result["mindmap"]["branches"] if b["label"] == "AI"]
        assert len(ai_branch) == 1
        child_labels = {ch["label"] for ch in ai_branch[0]["children"]}
        assert "GPT" in child_labels
        assert "Claude" in child_labels

    def test_mindmap_branches_capped_at_8(self):
        branches = [{"label": f"Branch{i}", "children": [{"label": "c", "timestamp": "00:00"}]}
                    for i in range(10)]
        c1 = self._chunk("T1", [{"section": "S", "key_points": ["p"]}], branches=branches[:5])
        c2 = self._chunk("T2", [{"section": "S2", "key_points": ["p"]}], branches=branches[5:])
        result = merge_chunk_outlines([c1, c2])
        assert len(result["mindmap"]["branches"]) <= 8

    def test_key_terms_dedup_keeps_longest_explanation(self):
        c1 = self._chunk("T1", [{"section": "S", "key_points": ["p"]}],
                          terms=[{"term": "LLM", "translation": "大模型", "explanation": "short"}])
        c2 = self._chunk("T2", [{"section": "S2", "key_points": ["p"]}],
                          terms=[{"term": "LLM", "translation": "大语言模型",
                                  "explanation": "Large Language Model used in this context for AI inference"}])
        result = merge_chunk_outlines([c1, c2])
        llm_terms = [t for t in result["key_terms"] if t["term"] == "LLM"]
        assert len(llm_terms) == 1
        assert "Large Language Model" in llm_terms[0]["explanation"]
        # Also keeps the longer translation
        assert llm_terms[0]["translation"] == "大语言模型"

    def test_mindmap_string_passthrough(self):
        """When first chunk's mindmap is a raw Mermaid string, use it as-is."""
        c1 = self._chunk("T1", [{"section": "S", "key_points": ["p"]}])
        c1["mindmap"] = "mindmap\n  root((Topic))"
        c2 = self._chunk("T2", [{"section": "S2", "key_points": ["p"]}])
        result = merge_chunk_outlines([c1, c2])
        assert isinstance(result["mindmap"], str)
        assert "root((Topic))" in result["mindmap"]


# ==================== Issue 5: PASS2 strategy thresholds ====================

class TestPass2StrategyThresholds:
    """Verify the threshold constants make sense."""

    def test_full_threshold_less_than_chunked(self):
        assert PASS2_FULL_THRESHOLD < PASS2_CHUNKED_THRESHOLD

    def test_full_threshold_200k(self):
        assert PASS2_FULL_THRESHOLD == 200_000

    def test_chunked_threshold_500k(self):
        assert PASS2_CHUNKED_THRESHOLD == 500_000


# ==================== Issue 7: compute_section_range ====================

class TestComputeSectionRange:
    def test_very_short_podcast(self):
        # < 5 min = 2-4
        assert compute_section_range(180, 0) == "2-4"

    def test_short_podcast(self):
        # 5-30 min = 3-7
        assert compute_section_range(600, 0) == "3-7"

    def test_normal_podcast(self):
        # 30-90 min = 5-12
        assert compute_section_range(3600, 0) == "5-12"

    def test_long_podcast(self):
        # > 90 min = 8-18
        assert compute_section_range(7200, 0) == "8-18"

    def test_boundary_5min(self):
        assert compute_section_range(299, 0) == "2-4"
        assert compute_section_range(300, 0) == "3-7"

    def test_boundary_30min(self):
        assert compute_section_range(1799, 0) == "3-7"
        assert compute_section_range(1800, 0) == "5-12"

    def test_boundary_90min(self):
        assert compute_section_range(5399, 0) == "5-12"
        assert compute_section_range(5400, 0) == "8-18"

    def test_fallback_to_tokens_when_no_duration(self):
        # 0 duration, 1000 tokens ≈ 2 min → 2-4
        assert compute_section_range(0, 1000) == "2-4"
        # 0 duration, 25000 tokens ≈ 50 min → 5-12
        assert compute_section_range(0, 25000) == "5-12"

    def test_zero_duration_zero_tokens(self):
        # Falls back to est_minutes=30 → 5-12
        assert compute_section_range(0, 0) == "5-12"


# ==================== Issue 8: _md_to_html_with_timestamps ====================

class TestMdToHtmlWithTimestamps:
    """Test the upgraded MD→HTML parser."""

    def test_fenced_code_block(self):
        md = "```python\nprint('hello')\n```"
        html = _md_to_html_with_timestamps(md)
        assert '<pre><code class="language-python">' in html
        assert "print(" in html
        assert "</code></pre>" in html

    def test_code_block_no_language(self):
        md = "```\nsome code\n```"
        html = _md_to_html_with_timestamps(md)
        assert "<pre><code>" in html
        assert "some code" in html

    def test_code_block_escapes_html(self):
        md = "```\n<script>alert('xss')</script>\n```"
        html = _md_to_html_with_timestamps(md)
        assert "&lt;script&gt;" in html
        assert "<script>" not in html

    def test_code_block_no_timestamp_linkification(self):
        md = "```\n[05:30] this is code\n```"
        html = _md_to_html_with_timestamps(md)
        assert 'data-time' not in html
        assert "[05:30]" in html

    def test_simple_table(self):
        md = "| Term | Meaning |\n|---|---|\n| LLM | Large Model |\n| RAG | Retrieval |"
        html = _md_to_html_with_timestamps(md)
        assert "<table>" in html
        assert "<thead>" in html
        assert "<tbody>" in html
        assert "<th>Term</th>" in html
        assert "<td>LLM</td>" in html
        assert "</table>" in html

    def test_table_html_escapes_cells(self):
        md = "| Key | Value |\n|---|---|\n| a<b | c>d |"
        html = _md_to_html_with_timestamps(md)
        assert "&lt;b" in html
        assert "c&gt;d" in html

    def test_nested_list_two_levels(self):
        md = "- Item 1\n  - Sub item A\n  - Sub item B\n- Item 2"
        html = _md_to_html_with_timestamps(md)
        # Should have nested <ul>
        assert html.count("<ul>") >= 2
        assert "Sub item A" in html
        assert "Item 2" in html

    def test_flat_list_preserved(self):
        md = "- Alpha\n- Beta\n- Gamma"
        html = _md_to_html_with_timestamps(md)
        assert "<ul>" in html
        assert "<li>" in html
        assert "Alpha" in html
        assert "Beta" in html
        assert "Gamma" in html

    def test_bold_in_list(self):
        md = "- **Key point**: details here"
        html = _md_to_html_with_timestamps(md)
        assert "<strong>Key point</strong>" in html

    def test_timestamp_linkification(self):
        md = "See [05:30] for details"
        html = _md_to_html_with_timestamps(md)
        assert 'data-time="330"' in html
        assert "05:30" in html

    def test_h2_header(self):
        md = "## Section Title"
        html = _md_to_html_with_timestamps(md)
        assert "<h2>Section Title</h2>" in html

    def test_h3_header(self):
        md = "### Sub Section"
        html = _md_to_html_with_timestamps(md)
        assert "<h3>Sub Section</h3>" in html

    def test_blockquote(self):
        md = "> This is a quote\n> With **bold**"
        html = _md_to_html_with_timestamps(md)
        assert "<blockquote>" in html
        assert "<strong>bold</strong>" in html

    def test_mixed_content(self):
        """Test a realistic insights.md snippet with multiple element types."""
        md = """## Quotes

> 「This is a great quote」
>
> — Speaker, [01:23:45]

- **Point 1**: something
- **Point 2**: another thing

```
code example
```

| Col A | Col B |
|---|---|
| val1 | val2 |"""
        html = _md_to_html_with_timestamps(md)
        assert "<h2>" in html
        assert "<blockquote>" in html
        assert "<li>" in html
        assert "<pre><code>" in html
        assert "<table>" in html

    def test_unclosed_code_block_handled(self):
        md = "```\nunclosed code block"
        html = _md_to_html_with_timestamps(md)
        assert "</code></pre>" in html


# ==================== Issue 3 (from prior session): _parse_insights_sections ====================

class TestParseInsightsSections:
    def test_chinese_headers(self):
        md = "## 一、金句\nquote1\n\n## 二、反共识观点\ncontra1\n\n## 三、灵感钩子\nhook1"
        result = _parse_insights_sections(md)
        assert "quote1" in result["quotes"]
        assert "contra1" in result["contrarian"]
        assert "hook1" in result["hooks"]

    def test_english_headers(self):
        md = "## Quotes\nquote1\n\n## Contrarian Takes\ncontra1\n\n## Inspiration Hooks\nhook1"
        result = _parse_insights_sections(md)
        assert "quote1" in result["quotes"]
        assert "contra1" in result["contrarian"]
        assert "hook1" in result["hooks"]

    def test_variant_headers_tolerated(self):
        md = "## Quotable Moments\nquote1\n\n## contrarian ideas\ncontra1\n\n## inspiration\nhook1"
        result = _parse_insights_sections(md)
        assert "quote1" in result["quotes"]
        assert "contra1" in result["contrarian"]
        assert "hook1" in result["hooks"]

    def test_unrecognized_header_warns(self, capsys):
        md = "## Random Header\ncontent\n\n## Quotes\nquote1"
        result = _parse_insights_sections(md)
        # Unrecognized header content should NOT appear in any section
        assert "content" not in result["quotes"]
        assert "content" not in result["contrarian"]
        assert "content" not in result["hooks"]
        # Warning should be printed to stderr
        captured = capsys.readouterr()
        assert "Random Header" in captured.err

    def test_empty_input(self):
        result = _parse_insights_sections("")
        assert result == {"quotes": "", "contrarian": "", "hooks": ""}
