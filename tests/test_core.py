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
from prepare import estimate_tokens, _cjk_ratio, chunk_segments, format_segment, fmt_timestamp, validate_audio_file
from render import validate_outline, validate_inputs, validate_timestamps, ts_to_seconds, fmt_date, detect_language


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
