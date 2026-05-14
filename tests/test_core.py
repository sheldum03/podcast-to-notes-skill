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
from prepare import estimate_tokens, _cjk_ratio, chunk_segments, format_segment, fmt_timestamp
from render import validate_outline, ts_to_seconds, fmt_date, detect_language


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
        # 14 CJK chars / 1.5 = ~9 tokens
        assert 6 <= tokens <= 15

    def test_mixed_text(self):
        text = "这是mixed混合text测试"
        tokens = estimate_tokens(text)
        assert tokens > 0
        # Should be between pure-CJK and pure-English estimates
        pure_cjk_est = int(len(text) / 1.5)
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
