"""
render.py — Render the agent's analysis output into MD or HTML.

Inputs:
  prep.json (from prepare.py): metadata, audio path
  outline.json (from agent's pass 1 + merge): structured outline + mindmap
  insights.md (from agent's pass 2): quotes + contrarian + hooks (Markdown)

Output: a single .md or .html file, ready to read.
"""

import argparse
import html as html_lib
import json
import re
import sys
from datetime import datetime
from pathlib import Path


# ==================== Helpers ====================

def ts_to_seconds(ts):
    if not ts:
        return 0
    parts = str(ts).strip("[]").split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        pass
    return 0


def fmt_date(date_str):
    if not date_str:
        return ""
    s = str(date_str)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


# ==================== UI labels by language ====================

LABELS = {
    "zh": {
        "tldr": "🎯 一句话总结",
        "outline": "📑 大纲",
        "quotes": "💎 金句",
        "contrarian": "🔄 反共识观点",
        "hooks": "🪝 灵感钩子",
        "mindmap": "🗺️ 思维导图",
        "glossary": "📖 术语表",
        "source": "来源",
        "date": "日期",
        "generated": "生成于",
        "search_placeholder": "🔍 搜索...",
        "trigger": "🪝 触发",
        "no_contrarian": "本期无明显反共识观点",
        "empty": "无",
        "term_col": "术语",
        "trans_col": "中文",
        "explain_col": "重要性",
    },
    "en": {
        "tldr": "🎯 TL;DR",
        "outline": "📑 Outline",
        "quotes": "💎 Quotes",
        "contrarian": "🔄 Contrarian Takes",
        "hooks": "🪝 Inspiration Hooks",
        "mindmap": "🗺️ Mind Map",
        "glossary": "📖 Glossary",
        "source": "Source",
        "date": "Date",
        "generated": "Generated",
        "search_placeholder": "🔍 Search...",
        "trigger": "🪝 Trigger",
        "no_contrarian": "No notable contrarian takes in this episode",
        "empty": "None",
        "term_col": "Term",
        "trans_col": "Translation",
        "explain_col": "Significance",
    },
}

# Field label patterns for parsing — used in both render and parse.
# (Chinese first, then English equivalent — both accepted in input.)
QUOTE_WHY_PATTERNS = [r"为什么值得记", r"Why memorable"]
CONTRA_LABELS_MAP = {  # canonical English → list of accepted labels
    "Take":            ["观点", "Take"],
    "Mainstream view": ["主流叙事", "Mainstream view", "Mainstream"],
    "Reasoning":       ["嘉宾理由", "Reasoning"],
    "Timestamp":       ["时间戳", "Timestamp"],
}
HOOK_TRIGGER_PATTERNS = [r"触发点", r"Trigger"]
HOOK_ACTION_PATTERNS = [r"可行动的延展", r"Actionable next step", r"Actionable"]
HOOK_PRIORITY_PATTERNS = [r"优先级", r"Priority"]


def detect_language(insights_md):
    """Detect output language from insights.md headers.
    Falls back to Chinese (the default)."""
    if not insights_md:
        return "zh"
    head_500 = insights_md[:500]
    en_markers = ["## Quotes", "## Contrarian", "## Inspiration"]
    if any(m in head_500 for m in en_markers):
        return "en"
    return "zh"


# ==================== Markdown Output ====================

def render_markdown(metadata, outline, insights_md, audio_url):
    title = metadata.get("title", "Untitled")
    date = fmt_date(metadata.get("upload_date", ""))
    lang = detect_language(insights_md)
    L = LABELS[lang]

    md = f"""# {title}

> **{L['source']}**: [{metadata.get('uploader', '')}]({metadata.get('webpage_url', audio_url)})  
> **{L['date']}**: {date}  
> **{L['generated']}**: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## {L['tldr']}

{outline.get('tldr', '')}

## {L['outline']}

"""
    for i, s in enumerate(outline.get("outline", []), 1):
        ts = s.get("timestamp", "")
        worth = s.get("worth_listening", True)
        marker = "" if worth else " ⏭️"
        md += f"### {i}. [{ts}] {s.get('section', '')}{marker}\n\n"
        for kp in s.get("key_points", []):
            md += f"- {kp}\n"
        if not worth and s.get("skip_reason"):
            md += f"\n> 💤 {s['skip_reason']}\n"
        md += "\n"

    # Mind map
    md += f"## {L['mindmap']}\n\n```mermaid\nmindmap\n"
    mm = outline.get("mindmap", {})
    md += f"  root(({mm.get('root', 'Topic' if lang == 'en' else '主题')}))\n"
    for branch in mm.get("branches", []):
        md += f"    {branch.get('label', '')}\n"
        for child in branch.get("children", []):
            ts = child.get("timestamp", "")
            md += f"      {f'[{ts}] ' if ts else ''}{child.get('label', '')}\n"
    md += "```\n\n---\n\n"
    md += insights_md
    md += "\n\n"

    if outline.get("key_terms"):
        md += f"## {L['glossary']}\n\n| {L['term_col']} | {L['trans_col']} | {L['explain_col']} |\n|---|---|---|\n"
        for t in outline["key_terms"]:
            md += f"| `{t.get('term', '')}` | {t.get('translation', '')} | {t.get('explanation', '')} |\n"

    return md


# ==================== HTML Simple ====================

def render_html_simple(metadata, outline, insights_md, audio_src, audio_url_web):
    """Single-column, embedded audio, clickable timestamps."""
    title = html_lib.escape(metadata.get("title", "Untitled"))
    date = fmt_date(metadata.get("upload_date", ""))
    lang = detect_language(insights_md)
    L = LABELS[lang]
    insights_html = _md_to_html_with_timestamps(insights_md)

    outline_html = _render_outline_html(outline)
    mindmap_text = _build_mermaid(outline.get("mindmap", {}))
    terms_html = _render_terms_html(outline.get("key_terms", []), L)

    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
mermaid.initialize({{ startOnLoad: true, theme: 'default' }});
</script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 850px; margin: 2em auto; padding: 1em; line-height: 1.6; color: #222; }}
  h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.3em; }}
  h2 {{ margin-top: 2em; color: #1a4d8c; }}
  .audio-player {{ position: sticky; top: 0; background: white; padding: 1em 0;
                   border-bottom: 1px solid #eee; z-index: 100; }}
  audio {{ width: 100%; }}
  .ts-link {{ color: #0070c9; text-decoration: none; font-family: monospace;
              background: #f0f7ff; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }}
  .ts-link:hover {{ background: #cce4ff; cursor: pointer; }}
  blockquote {{ border-left: 4px solid #1a4d8c; padding: 0.5em 1em; margin: 1em 0;
                background: #f8f9fa; }}
  .skip {{ opacity: 0.6; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ border: 1px solid #ddd; padding: 0.5em; text-align: left; }}
  th {{ background: #f0f0f0; }}
  code {{ background: #f4f4f4; padding: 2px 5px; border-radius: 3px; }}
  .meta {{ color: #666; font-size: 0.9em; }}
  .mermaid {{ background: #fafafa; padding: 1em; border-radius: 8px; }}
  @media (max-width: 600px) {{
    body {{ font-size: 16px; margin: 0.5em auto; padding: 0.75em; }}
    h1 {{ font-size: 22px; }}
    h2 {{ font-size: 18px; }}
    .ts-link {{ font-size: 14px; padding: 3px 8px; }}
    .meta {{ font-size: 13px; }}
  }}
</style>
</head>
<body>
<div class="audio-player">
  <audio id="audio" controls preload="metadata"><source src="{html_lib.escape(audio_src)}"></audio>
</div>
<h1>{title}</h1>
<div class="meta">
  <a href="{html_lib.escape(audio_url_web)}" target="_blank">{html_lib.escape(metadata.get('uploader', ''))}</a>
  · {date}
  · {L['generated']} {datetime.now().strftime('%Y-%m-%d %H:%M')}
</div>
<h2>{L['tldr']}</h2>
<p>{html_lib.escape(outline.get('tldr', ''))}</p>
<h2>{L['outline']}</h2>
{outline_html}
<h2>{L['mindmap']}</h2>
<div class="mermaid">{mindmap_text}</div>
<hr>
{insights_html}
{terms_html}
<script>
  document.querySelectorAll('.ts-link').forEach(link => {{
    link.addEventListener('click', e => {{
      e.preventDefault();
      const t = parseInt(link.dataset.time);
      const audio = document.getElementById('audio');
      audio.currentTime = t; audio.play();
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }});
  }});
</script>
</body>
</html>
"""


# ==================== HTML Dashboard (the dense one) ====================

def render_html_dashboard(metadata, outline, insights_md, audio_src, audio_url_web):
    """
    Editorial layout inspired by Figma's marketing system (per DESIGN.md):
    - Monochrome chrome: white canvas + black ink + figmaSans-like type
    - Each major section sits in an oversized pastel color block
      (lime / cream / lilac / navy / coral / mint) with rounded corners
    - White canvas between every two blocks
    - All CTAs are pills; eyebrow labels are uppercase mono with positive tracking
    """
    title = html_lib.escape(metadata.get("title", "Untitled"))
    date = fmt_date(metadata.get("upload_date", ""))
    lang = detect_language(insights_md)
    L = LABELS[lang]

    insights_parsed = _parse_insights_sections(insights_md)
    quotes_html = _render_quotes_dense(insights_parsed.get("quotes", ""), L)
    contrarian_html = _render_contrarian_dense(insights_parsed.get("contrarian", ""), L)
    hooks_html = _render_hooks_dense(insights_parsed.get("hooks", ""), L)

    outline_html = _render_outline_dense(outline)
    mindmap_text = _build_mermaid(outline.get("mindmap", {}))
    terms_html = _render_terms_dense(outline.get("key_terms", []), L)

    # Eyebrows in figmaMono uppercase. Strip emoji prefix from L labels for eyebrow use,
    # keep the original label for the headline.
    def _eyebrow(label):
        # Drop leading emoji + space if present
        parts = label.split(" ", 1)
        if len(parts) == 2 and not parts[0].isascii():
            return parts[1]
        return label

    # Build TOC anchors
    toc_items = [
        ('tldr', _eyebrow(L["tldr"])),
        ('outline', _eyebrow(L["outline"])),
        ('quotes', _eyebrow(L["quotes"])),
        ('contrarian', _eyebrow(L["contrarian"])),
        ('hooks', _eyebrow(L["hooks"])),
        ('mindmap', _eyebrow(L["mindmap"])),
        ('glossary', _eyebrow(L["glossary"])),
    ]
    toc_html = "\n".join(
        f'<a href="#{anchor}" class="toc-link">{html_lib.escape(label)}</a>'
        for anchor, label in toc_items
    )

    # Section numbering for eyebrows (01 / 02 / 03 …)
    def _num(n): return f"{n:02d}"

    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@320;330;340;400;480;540;700&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet">
<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
window.mermaid = mermaid;
mermaid.initialize({{ startOnLoad: true, theme: 'base', themeVariables: {{
  primaryColor: '#ffffff', primaryTextColor: '#000000',
  primaryBorderColor: '#000000', lineColor: '#000000',
  fontFamily: 'Inter, system-ui, sans-serif', fontSize: '14px'
}} }});
</script>
<style>
  :root {{
    /* Colors from DESIGN.md */
    --primary: #000000;
    --on-primary: #ffffff;
    --ink: #000000;
    --canvas: #ffffff;
    --inverse-canvas: #000000;
    --inverse-ink: #ffffff;
    --hairline: #e6e6e6;
    --hairline-soft: #f1f1f1;
    --surface-soft: #f7f7f5;
    /* Two pastel surfaces only (DESIGN.md spirit, but restrained palette) */
    --block-cream: #f4ecd6;
    --block-navy: #1f1d3d;
    /* Single accent color — use sparingly */
    --accent: #0066ff;
    --accent-soft: #e8efff;
    --semantic-success: #1ea64a;

    /* Spacing */
    --xxs: 4px; --xs: 8px; --sm: 12px; --md: 16px;
    --lg: 24px; --xl: 32px; --xxl: 48px; --section: 96px;

    /* Radius */
    --r-md: 8px; --r-lg: 24px; --r-xl: 32px;
    --r-pill: 50px; --r-full: 9999px;
  }}

  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    margin: 0;
    background: var(--canvas);
    color: var(--ink);
    font-family: 'Inter', 'figmaSans Fallback', -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif;
    font-feature-settings: 'kern' 1;
    font-size: 18px; font-weight: 320; line-height: 1.45; letter-spacing: -0.26px;
  }}
  .mono {{
    font-family: 'JetBrains Mono', 'figmaMono Fallback', 'SF Mono', menlo, monospace;
    text-transform: uppercase;
    letter-spacing: 0.54px;
  }}

  /* ============ TOP NAV (sticky white bar) ============ */
  .top-nav {{
    position: sticky; top: 0; z-index: 100;
    background: var(--canvas);
    border-bottom: 1px solid var(--hairline);
    height: auto; min-height: 56px;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 380px) auto auto;
    gap: var(--md);
    padding: var(--sm) var(--xl);
    align-items: center;
  }}
  .top-nav .brand {{
    min-width: 0; overflow: hidden;
  }}
  .top-nav .brand-title {{
    font-size: 20px; font-weight: 540; line-height: 1.3; letter-spacing: -0.2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .top-nav .brand-meta {{
    font-size: 12px; font-weight: 400; letter-spacing: 0.60px;
    text-transform: uppercase;
    color: var(--ink); opacity: 0.7;
    font-family: 'JetBrains Mono', monospace;
    margin-top: 2px;
  }}
  .top-nav audio {{ width: 100%; height: 36px; }}
  .top-nav .search {{
    border: 1px solid var(--hairline);
    background: var(--canvas);
    color: var(--ink);
    font-family: inherit; font-size: 16px; font-weight: 330;
    padding: 10px 14px;
    border-radius: var(--r-md);
    width: 200px;
  }}
  .top-nav .search:focus {{
    outline: none; border-color: var(--ink);
  }}
  .top-nav .pill {{
    background: var(--primary); color: var(--on-primary);
    font-family: inherit; font-size: 16px; font-weight: 480;
    letter-spacing: -0.10px;
    border: none;
    border-radius: var(--r-pill);
    padding: 10px 20px;
    cursor: pointer;
    white-space: nowrap;
  }}
  .top-nav .pill.secondary {{
    background: var(--canvas); color: var(--ink);
    border: 1px solid var(--ink);
  }}

  /* ============ MAIN STACK (single editorial column) ============ */
  main {{
    max-width: 1280px;
    margin: 0 auto;
    padding: var(--xxl) var(--xl);
    display: flex; flex-direction: column;
    gap: var(--section);
  }}

  /* ============ HERO ============ */
  .hero {{
    padding: var(--xxl) 0 0;
  }}
  .hero .eyebrow {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; font-weight: 400; letter-spacing: 0.60px;
    text-transform: uppercase;
    margin-bottom: var(--lg);
  }}
  .hero h1 {{
    margin: 0;
    font-size: clamp(40px, 6vw, 86px);
    font-weight: 340; line-height: 1.00;
    letter-spacing: -0.02em;
  }}
  .hero .meta-row {{
    margin-top: var(--xl);
    font-size: 16px; font-weight: 330; letter-spacing: -0.14px;
  }}
  .hero .meta-row a {{
    color: var(--ink); text-decoration: underline; text-decoration-thickness: 1px;
    text-underline-offset: 3px;
    transition: color 120ms;
  }}
  .hero .meta-row a:hover {{ color: var(--accent); }}

  /* ============ COLOR-BLOCK SECTION ============ */
  .block {{
    border-radius: var(--r-lg);
    padding: var(--xxl);
  }}
  /* Block surfaces: only cream + plain (white).
     "plain" lets a section use the .block padding/radius without a tinted background. */
  .block.plain   {{ background: var(--canvas); }}
  .block.cream   {{ background: var(--block-cream); }}

  .block .eyebrow {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; font-weight: 400; letter-spacing: 0.60px;
    text-transform: uppercase;
    opacity: 1;
    margin: 0 0 var(--lg);
    display: flex; align-items: baseline; gap: var(--sm);
  }}
  .block .eyebrow .num {{
    opacity: 0.5;
  }}

  .block h2 {{
    margin: 0 0 var(--lg);
    font-size: clamp(26px, 3vw, 40px);
    font-weight: 340; line-height: 1.10; letter-spacing: -0.96px;
  }}

  /* Plain (white) sections need a border to read as discrete chapters,
     since they no longer have a tinted background. */
  .block.plain {{
    border: 1px solid var(--hairline);
  }}

  /* ============ TLDR block — blue accent bar on the left ============ */
  .tldr-block {{
    border-left: 4px solid var(--accent);
  }}
  .tldr-block p {{
    margin: 0;
    font-size: clamp(20px, 2.2vw, 26px);
    font-weight: 340; line-height: 1.35; letter-spacing: -0.26px;
    max-width: 56ch;
  }}

  /* ============ OUTLINE block ============ */
  .outline-block .section-item {{
    border-top: 1px solid rgba(0,0,0,0.18);
    padding: var(--lg) 0;
    display: grid;
    grid-template-columns: 56px 1fr auto;
    gap: var(--lg);
    align-items: start;
  }}
  .outline-block .section-item:last-child {{
    border-bottom: 1px solid rgba(0,0,0,0.18);
  }}
  .outline-block .section-item.skip {{ opacity: 0.55; }}
  .outline-block .num {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; letter-spacing: 0.60px; text-transform: uppercase;
    line-height: 1.45; padding-top: 8px;
  }}
  .outline-block .body h3 {{
    margin: 0 0 var(--xs);
    font-size: 24px; font-weight: 700; line-height: 1.45; letter-spacing: 0;
  }}
  .outline-block .body ul {{
    margin: var(--xs) 0 0; padding-left: 0; list-style: none;
  }}
  .outline-block .body li {{
    font-size: 16px; font-weight: 330; line-height: 1.55; letter-spacing: -0.14px;
    padding-left: var(--md);
    position: relative;
  }}
  .outline-block .body li::before {{
    content: '—'; position: absolute; left: 0; opacity: 0.55;
  }}
  .outline-block .skip-note {{
    margin-top: var(--xs);
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; letter-spacing: 0.60px; text-transform: uppercase;
    opacity: 0.6;
  }}

  /* ============ TIMESTAMP PILL ============ */
  .ts {{
    display: inline-block;
    background: var(--canvas); color: var(--ink);
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; font-weight: 400;
    padding: 4px 10px; border-radius: var(--r-pill);
    text-decoration: none; cursor: pointer;
    line-height: 1.2; white-space: nowrap;
    border: 1px solid var(--ink);
    transition: background 120ms, color 120ms, border-color 120ms;
  }}
  .ts:hover {{
    background: var(--accent); color: var(--on-primary); border-color: var(--accent);
  }}

  /* ============ QUOTES — blue left bar on each card ============ */
  .quotes-block .quote-grid {{
    display: grid; gap: var(--lg);
    grid-template-columns: 1fr;
  }}
  @media (min-width: 800px) {{
    .quotes-block .quote-grid {{ grid-template-columns: repeat(2, 1fr); }}
  }}
  .quote-card {{
    background: var(--canvas);
    border: 1px solid var(--hairline);
    border-left: 3px solid var(--accent);
    border-radius: var(--r-md);
    padding: var(--lg);
  }}
  .quote-card .zh {{
    font-size: 20px; font-weight: 540; line-height: 1.35; letter-spacing: -0.2px;
    margin-bottom: var(--sm);
  }}
  .quote-card .en {{
    font-size: 14px; font-weight: 330; line-height: 1.5; letter-spacing: -0.14px;
    opacity: 0.7; margin-bottom: var(--md);
    font-style: italic;
  }}
  .quote-card .meta {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; letter-spacing: 0.60px; text-transform: uppercase;
    display: flex; gap: var(--sm); align-items: center; flex-wrap: wrap;
    margin-bottom: var(--sm);
  }}
  .quote-card .why {{
    border-top: 1px solid var(--hairline);
    padding-top: var(--sm); margin-top: var(--sm);
    font-size: 14px; font-weight: 330; line-height: 1.45;
  }}

  /* ============ CONTRARIAN block (cream, white inner cards) ============ */
  .contra-block .contra-card {{
    background: var(--canvas);
    border-radius: var(--r-md);
    padding: var(--lg);
    margin-bottom: var(--md);
  }}
  .contra-block .contra-card:last-child {{ margin-bottom: 0; }}
  .contra-block .contra-card .label {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; letter-spacing: 0.60px; text-transform: uppercase;
    opacity: 0.65; display: block; margin-bottom: 4px;
  }}
  .contra-block .contra-card .text {{
    font-size: 16px; font-weight: 330; line-height: 1.55;
    margin-bottom: var(--sm);
  }}
  .contra-block .contra-card .text:last-child {{ margin-bottom: 0; }}
  .contra-block .empty-state {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; letter-spacing: 0.60px; text-transform: uppercase;
    opacity: 0.7;
  }}

  /* ============ HOOKS block (coral) ============ */
  .hooks-block .hook-card {{
    background: var(--canvas);
    border-radius: var(--r-md);
    padding: var(--lg);
    margin-bottom: var(--md);
    display: grid; grid-template-columns: 1fr auto; gap: var(--md);
    align-items: start;
  }}
  .hooks-block .hook-card:last-child {{ margin-bottom: 0; }}
  .hooks-block .hook-card .body .trigger {{
    font-size: 18px; font-weight: 480; line-height: 1.45; letter-spacing: -0.14px;
    margin-bottom: var(--xs);
  }}
  .hooks-block .hook-card .body .action {{
    font-size: 16px; font-weight: 330; line-height: 1.55;
    background: var(--surface-soft);
    border-radius: var(--r-md);
    padding: var(--sm) var(--md);
    margin-top: var(--sm);
  }}
  .hooks-block .priority {{
    background: var(--ink); color: var(--on-primary);
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; letter-spacing: 0.60px; text-transform: uppercase;
    padding: 4px 10px; border-radius: var(--r-pill);
    align-self: start;
    white-space: nowrap;
  }}
  .hooks-block .priority.medium {{
    background: var(--accent); color: var(--on-primary);
  }}
  .hooks-block .priority.low {{
    background: var(--canvas); color: var(--ink); border: 1px solid var(--ink);
  }}

  /* ============ MINDMAP block (mint) ============ */
  .mindmap-block .mermaid {{
    background: var(--canvas);
    border-radius: var(--r-md);
    padding: var(--xl);
    text-align: center;
  }}
  .mindmap-block .mermaid svg {{ max-width: 100%; height: auto; }}

  /* ============ GLOSSARY (white canvas, hairline) ============ */
  .glossary-block {{ padding: 0; background: transparent; }}
  .glossary-block .eyebrow {{ margin-bottom: var(--lg); }}
  .glossary-block h2 {{ margin-bottom: var(--lg); color: var(--ink); }}
  .glossary-block table {{
    width: 100%; border-collapse: collapse;
    font-size: 16px; font-weight: 330; line-height: 1.45;
  }}
  .glossary-block th, .glossary-block td {{
    text-align: left; padding: var(--md) var(--sm);
    border-bottom: 1px solid var(--hairline-soft);
    vertical-align: top;
  }}
  .glossary-block th {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; font-weight: 400; letter-spacing: 0.60px;
    text-transform: uppercase;
    color: var(--ink); opacity: 0.7;
    border-bottom: 1px solid var(--ink);
  }}
  .glossary-block code {{
    background: var(--surface-soft);
    padding: 2px 8px;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 14px;
  }}
  .glossary-block .empty {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; letter-spacing: 0.60px; text-transform: uppercase;
    opacity: 0.6;
  }}

  /* ============ FOOTER STRIP ============ */
  footer {{
    margin-top: var(--section);
    border-top: 1px solid var(--ink);
    padding: var(--xxl) var(--xl);
    max-width: 1280px;
    margin-left: auto; margin-right: auto;
    display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: var(--md);
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px; letter-spacing: 0.60px; text-transform: uppercase;
  }}
  footer .nav-links a {{
    color: var(--ink); text-decoration: none; margin-right: var(--md);
  }}
  footer .nav-links a:hover {{ text-decoration: underline; }}

  /* ============ SEARCH HIDDEN STATE ============ */
  .hidden {{ display: none !important; }}

  /* ============ MOBILE ============ */
  @media (max-width: 768px) {{
    body {{ font-size: 16px; }}
    .top-nav {{
      grid-template-columns: 1fr;
      gap: var(--xs);
      padding: var(--sm) var(--md);
    }}
    .top-nav .search {{ width: 100%; }}
    main {{
      padding: var(--lg) var(--md);
      gap: var(--xxl);
    }}
    .block {{
      border-radius: 0;
      margin-left: calc(var(--md) * -1);
      margin-right: calc(var(--md) * -1);
      padding: var(--xl) var(--md);
    }}
    .outline-block .section-item {{
      grid-template-columns: 1fr;
      gap: var(--xs);
    }}
    .hooks-block .hook-card {{
      grid-template-columns: 1fr;
    }}
    footer {{
      padding: var(--xl) var(--md);
      flex-direction: column;
      align-items: flex-start;
    }}
  }}
</style>
</head>
<body>

<nav class="top-nav">
  <div class="brand">
    <div class="brand-title">{title}</div>
    <div class="brand-meta">
      <a href="{html_lib.escape(audio_url_web)}" target="_blank" style="color: inherit; text-decoration: none;">{html_lib.escape(metadata.get('uploader', ''))}</a>
      {' · ' + date if date else ''}
    </div>
  </div>
  <audio id="audio" controls preload="metadata">
    <source src="{html_lib.escape(audio_src)}">
  </audio>
  <input type="text" class="search" id="search" placeholder="{html_lib.escape(L['search_placeholder'])}" autocomplete="off">
  <button class="pill" onclick="window.open('{html_lib.escape(audio_url_web)}','_blank')">Open source</button>
</nav>

<main>

  <!-- HERO -->
  <section class="hero">
    <div class="eyebrow">{html_lib.escape(L["tldr"])} · {date or '—'}</div>
    <h1>{title}</h1>
    <div class="meta-row mono" style="opacity: 0.6;">
      {_eyebrow(L["source"])} — <a href="{html_lib.escape(audio_url_web)}" target="_blank">{html_lib.escape(metadata.get('uploader', ''))}</a>
    </div>
  </section>

  <!-- TLDR (plain white + blue accent bar via CSS) -->
  <section class="block plain tldr-block" id="tldr">
    <div class="eyebrow"><span class="num">{_num(1)}</span><span>{html_lib.escape(_eyebrow(L["tldr"]))}</span></div>
    <p>{html_lib.escape(outline.get('tldr', ''))}</p>
  </section>

  <!-- OUTLINE (plain white) -->
  <section class="block plain outline-block" id="outline">
    <div class="eyebrow"><span class="num">{_num(2)}</span><span>{html_lib.escape(_eyebrow(L["outline"]))}</span></div>
    <h2>{html_lib.escape(_eyebrow(L["outline"]))}</h2>
    {outline_html}
  </section>

  <!-- QUOTES (plain white, cards carry the blue accent) -->
  <section class="block plain quotes-block" id="quotes">
    <div class="eyebrow"><span class="num">{_num(3)}</span><span>{html_lib.escape(_eyebrow(L["quotes"]))}</span></div>
    <h2>{html_lib.escape(_eyebrow(L["quotes"]))}</h2>
    <div class="quote-grid">
      {quotes_html}
    </div>
  </section>

  <!-- CONTRARIAN (cream) -->
  <section class="block cream contra-block" id="contrarian">
    <div class="eyebrow"><span class="num">{_num(4)}</span><span>{html_lib.escape(_eyebrow(L["contrarian"]))}</span></div>
    <h2>{html_lib.escape(_eyebrow(L["contrarian"]))}</h2>
    {contrarian_html}
  </section>

  <!-- HOOKS (cream — the only warm pastel) -->
  <section class="block cream hooks-block" id="hooks">
    <div class="eyebrow"><span class="num">{_num(5)}</span><span>{html_lib.escape(_eyebrow(L["hooks"]))}</span></div>
    <h2>{html_lib.escape(_eyebrow(L["hooks"]))}</h2>
    {hooks_html}
  </section>

  <!-- MINDMAP (plain white) -->
  <section class="block plain mindmap-block" id="mindmap">
    <div class="eyebrow"><span class="num">{_num(6)}</span><span>{html_lib.escape(_eyebrow(L["mindmap"]))}</span></div>
    <h2>{html_lib.escape(_eyebrow(L["mindmap"]))}</h2>
    <div class="mermaid">{mindmap_text}</div>
  </section>

  <!-- GLOSSARY (white, no block padding — narrow column) -->
  <section class="glossary-block" id="glossary">
    <div class="eyebrow"><span class="num">{_num(7)}</span><span>{html_lib.escape(_eyebrow(L["glossary"]))}</span></div>
    <h2>{html_lib.escape(_eyebrow(L["glossary"]))}</h2>
    {terms_html}
  </section>

</main>

<footer>
  <div>{L['generated']} {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
  <div class="nav-links">
    {toc_html}
  </div>
</footer>

<script>
  // Click timestamp → seek audio + scroll to top
  document.body.addEventListener('click', e => {{
    const link = e.target.closest('.ts');
    if (!link) return;
    e.preventDefault();
    const t = parseInt(link.dataset.time);
    const audio = document.getElementById('audio');
    if (!isNaN(t)) {{
      audio.currentTime = t;
      audio.play();
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }}
  }});

  // Search: filter sections, quotes, hooks, contrarian, glossary rows
  const search = document.getElementById('search');
  const searchableSelector = '.section-item, .quote-card, .contra-card, .hook-card, .term-row';
  search.addEventListener('input', e => {{
    const q = e.target.value.trim().toLowerCase();
    document.querySelectorAll(searchableSelector).forEach(el => {{
      if (!q) {{ el.classList.remove('hidden'); return; }}
      const txt = el.textContent.toLowerCase();
      el.classList.toggle('hidden', !txt.includes(q));
    }});
  }});
</script>

</body>
</html>
"""



# ==================== Helpers for HTML rendering ====================

def _build_mermaid(mm):
    if not mm:
        return ""
    text = "mindmap\n"
    text += f"  root(({_mm_escape(mm.get('root', '主题'))}))\n"
    for branch in mm.get("branches", []):
        text += f"    {_mm_escape(branch.get('label', ''))}\n"
        for child in branch.get("children", []):
            ts = child.get("timestamp", "")
            label = child.get("label", "")
            prefix = f"[{ts}] " if ts else ""
            text += f"      {_mm_escape(prefix + label)}\n"
    return text


def _mm_escape(s):
    """Mermaid is sensitive to certain chars in labels."""
    return str(s).replace("(", "（").replace(")", "）").replace("[", "【").replace("]", "】")


def _render_outline_html(outline):
    """For html_simple (open layout)."""
    out = ""
    for i, s in enumerate(outline.get("outline", []), 1):
        ts = s.get("timestamp", "")
        secs = ts_to_seconds(ts)
        worth_class = "" if s.get("worth_listening", True) else " skip"
        marker = "" if s.get("worth_listening", True) else " ⏭️"
        out += f'<div class="section{worth_class}">'
        out += f'<h3><a href="#" class="ts-link" data-time="{secs}">[{ts}]</a> '
        out += f'{i}. {html_lib.escape(s.get("section", ""))}{marker}</h3>'
        out += '<ul>'
        for kp in s.get("key_points", []):
            out += f'<li>{html_lib.escape(kp)}</li>'
        out += '</ul>'
        if not s.get("worth_listening", True) and s.get("skip_reason"):
            out += f'<div class="skip-note">💤 {html_lib.escape(s["skip_reason"])}</div>'
        out += '</div>'
    return out


def _render_outline_dense(outline):
    """For html_dashboard (Figma editorial style).
    Three-column grid row: [num] [body with title + bullets] [timestamp pill]
    """
    out = ""
    for i, s in enumerate(outline.get("outline", []), 1):
        ts = s.get("timestamp", "")
        secs = ts_to_seconds(ts)
        worth = s.get("worth_listening", True)
        skip_class = " skip" if not worth else ""
        marker = "" if worth else " ⏭"
        out += f'<div class="section-item{skip_class}" id="sec-{i}">'
        out += f'<div class="num">{i:02d}</div>'
        out += '<div class="body">'
        out += f'<h3>{html_lib.escape(s.get("section", ""))}{marker}</h3>'
        if s.get("key_points"):
            out += '<ul>'
            for kp in s["key_points"]:
                out += f'<li>{html_lib.escape(kp)}</li>'
            out += '</ul>'
        if not worth and s.get("skip_reason"):
            out += f'<div class="skip-note">{html_lib.escape(s["skip_reason"])}</div>'
        out += '</div>'
        if ts:
            out += f'<a class="ts" data-time="{secs}">{ts}</a>'
        else:
            out += '<span></span>'
        out += '</div>'
    return out


def _render_terms_html(terms, L=None):
    if not terms:
        return ""
    if L is None:
        L = LABELS["zh"]
    out = f'<h2>{L["glossary"]}</h2><table><tr><th>{L["term_col"]}</th><th>{L["trans_col"]}</th><th>{L["explain_col"]}</th></tr>'
    for t in terms:
        out += f"<tr><td><code>{html_lib.escape(t.get('term', ''))}</code></td>"
        out += f"<td>{html_lib.escape(t.get('translation', ''))}</td>"
        out += f"<td>{html_lib.escape(t.get('explanation', ''))}</td></tr>"
    out += '</table>'
    return out


def _render_terms_dense(terms, L=None):
    if L is None:
        L = LABELS["zh"]
    if not terms:
        return f'<p class="empty">{L["empty"]}</p>'
    out = '<table>'
    out += f'<tr><th>{L["term_col"]}</th><th>{L["trans_col"]}</th><th>{L["explain_col"]}</th></tr>'
    for t in terms:
        out += '<tr class="term-row">'
        out += f'<td><code>{html_lib.escape(t.get("term", ""))}</code></td>'
        out += f'<td>{html_lib.escape(t.get("translation", ""))}</td>'
        out += f'<td>{html_lib.escape(t.get("explanation", ""))}</td>'
        out += '</tr>'
    out += '</table>'
    return out


# ==================== Insights MD parsing ====================

def _parse_insights_sections(md):
    """
    Split insights markdown into quotes / contrarian / hooks.
    Looks for the canonical headers from the prompt.
    """
    sections = {"quotes": "", "contrarian": "", "hooks": ""}
    current = None
    for line in md.split("\n"):
        stripped = line.strip()
        if "## " in stripped and ("金句" in stripped or "Quotable" in stripped or "Quotes" in stripped):
            current = "quotes"
            continue
        elif "## " in stripped and ("反共识" in stripped or "Contrarian" in stripped):
            current = "contrarian"
            continue
        elif "## " in stripped and ("灵感" in stripped or "Hooks" in stripped or "Inspiration" in stripped):
            current = "hooks"
            continue
        if current:
            sections[current] += line + "\n"
    return sections


def _render_quotes_dense(quotes_md, L=None):
    """Parse the quote blockquote structure into compact cards.

    Supports both Chinese (with 「」 wrappers) and English (no wrappers,
    italic for original).
    """
    if L is None:
        L = LABELS["zh"]
    if not quotes_md.strip():
        return f"<p style='color:var(--text-soft)'>{L['empty']}</p>"
    blocks = re.split(r"\n\s*\n", quotes_md.strip())
    out = ""
    why_pat = re.compile(
        r"^💡\s*\*?\*?(?:" + "|".join(QUOTE_WHY_PATTERNS) + r")\*?\*?[:：]?\s*"
    )
    for block in blocks:
        if not block.strip().startswith(">"):
            continue
        lines = [l.lstrip(">").strip() for l in block.split("\n") if l.strip()]
        translation, original, meta_line, why = "", "", "", ""
        for ln in lines:
            if "「" in ln and "」" in ln:
                # Chinese-style translation
                translation = ln.replace("「", "").replace("」", "").strip()
            elif ln.startswith("*") and ln.endswith("*") and len(ln) > 2 and not ln.startswith("**"):
                # Italicized — the English original
                original = ln.strip("*").strip()
            elif ln.startswith("—") or ln.startswith("--"):
                meta_line = ln.lstrip("—-").strip()
            elif "💡" in ln or any(p in ln for p in QUOTE_WHY_PATTERNS):
                why = why_pat.sub("", ln).strip()
            elif not translation and not ln.startswith(("*", "—", "💡")):
                # Plain text line — English-style quote with no special wrapping
                translation = ln.strip().strip('"').strip("'")
        if not translation and not original:
            continue
        out += '<div class="quote-card">'
        if translation:
            # Chinese style uses 「」, English uses curly quotes
            if any("\u4e00" <= ch <= "\u9fff" for ch in translation):
                out += f'<div class="zh">「{html_lib.escape(translation)}」</div>'
            else:
                out += f'<div class="zh">"{html_lib.escape(translation)}"</div>'
        if original and original != translation:
            out += f'<div class="en">{html_lib.escape(original)}</div>'
        if meta_line:
            out += f'<div class="meta">— {_linkify_timestamps(html_lib.escape(meta_line))}</div>'
        if why:
            out += f'<div class="why">{html_lib.escape(why)}</div>'
        out += '</div>'
    return out or f'<p class="empty-state">{L["empty"]}</p>'


def _render_contrarian_dense(contra_md, L=None):
    if L is None:
        L = LABELS["zh"]
    if not contra_md.strip() or "无明显反共识" in contra_md or "No notable contrarian" in contra_md:
        return f'<p class="empty-state">{L["no_contrarian"]}</p>'
    blocks = re.split(r"\n\s*\n", contra_md.strip())
    out = ""
    for block in blocks:
        if not block.strip().startswith("-"):
            continue
        item = '<div class="contra-card">'
        for line in block.split("\n"):
            ln = line.lstrip("-").strip()
            m = re.match(r"\*\*([^*]+)\*\*[:：]\s*(.+)", ln)
            if m:
                item += f'<div><span class="label">{html_lib.escape(m.group(1))}</span></div>'
                item += f'<div class="text">{_linkify_timestamps(html_lib.escape(m.group(2)))}</div>'
        item += "</div>"
        out += item
    return out or f'<p class="empty-state">{L["no_contrarian"]}</p>'


def _render_hooks_dense(hooks_md, L=None):
    """Hook cards with two-column grid: [body (trigger + action)] [priority pill]."""
    if L is None:
        L = LABELS["zh"]
    if not hooks_md.strip():
        return f'<p class="empty">{L["empty"]}</p>'
    blocks = re.split(r"\n\s*\n", hooks_md.strip())
    out = ""
    trigger_pat = re.compile(
        r"^.*?🪝\s*\*?\*?(?:" + "|".join(HOOK_TRIGGER_PATTERNS) + r")\*?\*?[:：]?\s*"
    )
    action_pat = re.compile(
        r"^.*?🎯\s*\*?\*?(?:" + "|".join(HOOK_ACTION_PATTERNS) + r")\*?\*?[:：]?\s*"
    )
    priority_pat = re.compile(
        r"^.*?📌\s*\*?\*?(?:" + "|".join(HOOK_PRIORITY_PATTERNS) + r")\*?\*?[:：]?\s*"
    )
    for block in blocks:
        if "🪝" not in block:
            continue
        trigger, action, priority = "", "", ""
        for line in block.split("\n"):
            ln = line.lstrip("-").strip()
            if "🪝" in ln:
                trigger = trigger_pat.sub("", ln)
            elif "🎯" in ln:
                action = action_pat.sub("", ln)
            elif "📌" in ln:
                priority = priority_pat.sub("", ln).strip()
        prio_class = "high"
        prio_lower = priority.lower()
        if "中" in priority or "medium" in prio_lower or "med" in prio_lower:
            prio_class = "medium"
        elif "低" in priority or "low" in prio_lower:
            prio_class = "low"
        prio_label = priority or ("High" if "en" == _lang_from_labels(L) else "高")

        out += '<div class="hook-card">'
        out += '<div class="body">'
        out += f'<div class="trigger">{_linkify_timestamps(html_lib.escape(trigger))}</div>'
        if action:
            out += f'<div class="action">{_linkify_timestamps(html_lib.escape(action))}</div>'
        out += '</div>'
        out += f'<span class="priority {prio_class}">{html_lib.escape(prio_label)}</span>'
        out += '</div>'
    return out or f'<p class="empty">{L["empty"]}</p>'


def _lang_from_labels(L):
    """Reverse lookup: which language are we in based on a labels dict."""
    return "en" if L.get("outline") == "📑 Outline" else "zh"


def _linkify_timestamps(escaped_text):
    """Given an HTML-escaped string, replace [HH:MM:SS] with clickable .ts spans."""
    return re.sub(
        r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]",
        lambda m: f'<a class="ts" data-time="{ts_to_seconds(m.group(1))}">{m.group(1)}</a>',
        escaped_text,
    )


def _md_to_html_with_timestamps(md):
    """Lightweight MD → HTML for the simple format."""
    lines = md.split("\n")
    out = []
    in_blockquote = False
    in_list = False

    for line in lines:
        if line.startswith("## "):
            if in_list: out.append("</ul>"); in_list = False
            if in_blockquote: out.append("</blockquote>"); in_blockquote = False
            out.append(f"<h2>{html_lib.escape(line[3:].strip())}</h2>")
            continue
        if line.startswith("### "):
            if in_list: out.append("</ul>"); in_list = False
            out.append(f"<h3>{html_lib.escape(line[4:].strip())}</h3>")
            continue
        if line.startswith("> "):
            if not in_blockquote:
                out.append("<blockquote>"); in_blockquote = True
            content = line[2:].strip()
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_lib.escape(content))
            content = _linkify_timestamps(content)
            out.append(f"<p>{content}</p>")
            continue
        if in_blockquote and not line.strip():
            out.append("</blockquote>"); in_blockquote = False; continue
        if in_blockquote:
            out.append(f"<p>{html_lib.escape(line.strip())}</p>"); continue
        if line.startswith("- ") or line.startswith("* "):
            if not in_list: out.append("<ul>"); in_list = True
            content = line[2:].strip()
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_lib.escape(content))
            content = _linkify_timestamps(content)
            out.append(f"<li>{content}</li>")
            continue
        if in_list and not line.strip():
            out.append("</ul>"); in_list = False; continue
        if line.strip():
            content = html_lib.escape(line.strip())
            content = _linkify_timestamps(content)
            out.append(f"<p>{content}</p>")

    if in_blockquote: out.append("</blockquote>")
    if in_list: out.append("</ul>")
    return "\n".join(out)


# ==================== Main ====================

def slugify(s, max_len=60):
    s = re.sub(r"[^\w\s-]", "", s).strip()
    s = re.sub(r"[\s_]+", "-", s)
    return s[:max_len].strip("-") or "untitled"


def validate_outline(outline, source_path):
    """Validate outline.json structure before rendering.

    Checks required top-level keys, types, and nested fields.
    Prints a clear error and exits if validation fails.
    """
    errors = []

    # 1. Required top-level keys
    required_keys = ["tldr", "outline", "mindmap", "key_terms"]
    missing = [k for k in required_keys if k not in outline]
    if missing:
        errors.append(f"Missing required top-level keys: {', '.join(missing)}")

    # 2. 'outline' must be a list with valid items
    if "outline" in outline:
        if not isinstance(outline["outline"], list):
            errors.append("'outline' must be a list, got "
                          f"{type(outline['outline']).__name__}")
        else:
            for i, item in enumerate(outline["outline"]):
                if not isinstance(item, dict):
                    errors.append(f"outline[{i}] must be an object, got "
                                  f"{type(item).__name__}")
                    continue
                item_missing = []
                if "section" not in item:
                    item_missing.append("section")
                if "key_points" not in item:
                    item_missing.append("key_points")
                if item_missing:
                    errors.append(f"outline[{i}] missing required fields: "
                                  f"{', '.join(item_missing)}")

    # 3. 'mindmap' must be a string (Mermaid syntax) or a dict (structured)
    if "mindmap" in outline:
        if not isinstance(outline["mindmap"], (str, dict)):
            errors.append("'mindmap' must be a string or object, got "
                          f"{type(outline['mindmap']).__name__}")

    # 4. 'key_terms' must be a list
    if "key_terms" in outline:
        if not isinstance(outline["key_terms"], list):
            errors.append("'key_terms' must be a list, got "
                          f"{type(outline['key_terms']).__name__}")

    if errors:
        print(f"ERROR: outline.json validation failed ({source_path}):",
              file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print("\nThe LLM-generated outline.json is malformed. "
              "Please re-run Step 3 (LLM analysis) to regenerate it.",
              file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prep", required=True, help="prep.json path")
    parser.add_argument("--outline", required=True, help="outline.json path")
    parser.add_argument("--insights", required=True, help="insights.md path")
    parser.add_argument("--format", choices=["md", "html_simple", "html_dashboard"],
                        default="html_dashboard")
    parser.add_argument("--output", help="output path (default: based on title)")
    args = parser.parse_args()

    prep = json.loads(Path(args.prep).read_text(encoding="utf-8"))

    outline_path = Path(args.outline)
    try:
        outline = json.loads(outline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse outline.json ({outline_path}): {e}",
              file=sys.stderr)
        print("\nThe file is not valid JSON. "
              "Please re-run Step 3 (LLM analysis) to regenerate it.",
              file=sys.stderr)
        sys.exit(1)

    validate_outline(outline, outline_path)

    insights = Path(args.insights).read_text(encoding="utf-8")

    metadata = prep.get("metadata", {})
    audio_path = metadata.get("audio_path", "")
    audio_url_web = metadata.get("webpage_url", prep.get("url", ""))

    # Use relative path for HTML so the file is portable when bundled with audio
    output_dir = Path(args.prep).parent
    if args.output:
        out_path = Path(args.output)
    else:
        slug = slugify(metadata.get("title", "podcast-notes"))
        ext = "md" if args.format == "md" else "html"
        out_path = output_dir / f"{slug}.{ext}"

    # Compute audio src as relative if possible
    try:
        audio_src = str(Path(audio_path).resolve().relative_to(out_path.parent.resolve()))
    except (ValueError, FileNotFoundError):
        audio_src = audio_path  # fall back to absolute

    if args.format == "md":
        content = render_markdown(metadata, outline, insights, audio_url_web)
    elif args.format == "html_simple":
        content = render_html_simple(metadata, outline, insights, audio_src, audio_url_web)
    else:
        content = render_html_dashboard(metadata, outline, insights, audio_src, audio_url_web)

    out_path.write_text(content, encoding="utf-8")
    print(f"✅ Rendered: {out_path}")
    print(f"   Format: {args.format}")
    print(f"   Size: {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
