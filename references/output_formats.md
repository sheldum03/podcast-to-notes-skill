# Output Formats

Three formats from `render.py`. Pick based on use case.

## md — Standard Markdown

```bash
python scripts/render.py --prep prep.json --outline outline.json --insights insights.md --format md
```

Best for:
- Obsidian / note-taking apps
- Pasting into GitHub / GitLab issues / wikis
- Anywhere with native Mermaid support
- Version control / diffing

Layout: linear single-column. TL;DR → outline → mindmap (Mermaid block) → quotes → contrarian → hooks → glossary.

Limitations: no audio playback, timestamps are plain text (work in some podcast players if formatted MM:SS).

## html_simple — Single-column HTML

```bash
python scripts/render.py --format html_simple ...
```

Best for:
- Mobile reading
- Sharing with non-technical users (just open in browser)
- Quick review on the same machine that has the audio file

Layout: max-width 850px, single column. Sticky audio player at top. Click any `[HH:MM:SS]` to jump audio there.

Layout looks similar to a long blog post.

## html_dashboard — Information-dense layout

```bash
python scripts/render.py --format html_dashboard ...   # default
```

Best for:
- Desktop study/review (ideally 1280px+ wide)
- Scanning lots of content quickly
- Re-finding specific quotes after first read
- Power users who want everything visible at once

Layout features:

```
┌─────────────────────────────────────────────────────────────┐
│  [Title + meta] [Audio player ▶] [🔍 Search] [🌓 Theme]      │ ← Sticky top bar
├──────────┬──────────────────────────────────────────────────┤
│          │  ╔═══ TL;DR ═══════════════════════════════╗     │
│  TOC     │  ╚═════════════════════════════════════════╝     │
│  ────    │                                                  │
│ TLDR     │  ┌── 📑 大纲 ──┐  ┌── 💎 金句 ────────┐           │
│ Outline  │  │ 1. [00:05]  │  │ Quote 1 ZH        │           │
│  1. ...  │  │   point...  │  │ Quote 1 EN       │           │
│  2. ...  │  │ 2. [00:12]  │  │ — speaker [12:30]│           │
│  3. ...  │  │   point...  │  └──────────────────┘           │
│ Quotes   │  │ ...         │  ┌── 🔄 反共识 ───────┐          │
│ Contra   │  └─────────────┘  │ Take 1            │           │
│ Hooks    │                   └──────────────────┘           │
│ Mindmap  │                   ┌── 🪝 灵感 ────────┐          │
│ Glossary │                   │ Hook 1 [priority] │           │
│          │                   └──────────────────┘           │
│          │                                                  │
│          │  ┌── 🗺️ Mind map (Mermaid) ───────────┐          │
│          │  └────────────────────────────────────┘          │
│          │  ┌── 📖 Glossary table ───────────────┐          │
│          │  └────────────────────────────────────┘          │
└──────────┴──────────────────────────────────────────────────┘
```

Features:
- **Sticky audio player at top** — always available while scrolling
- **Sticky left sidebar TOC** — jump to any section instantly
- **Two-column main** — outline on left, quotes/contrarian/hooks stacked on right (so you can compare structural breakdown vs key takeaways side by side)
- **Click any timestamp `[12:30]`** anywhere → audio jumps there
- **Search box** — live-filters all visible cards (sections, quotes, hooks, terms)
- **Dark mode toggle** — persisted in localStorage
- **Mobile fallback** — collapses to single column under 1100px

Information density: ~3x more visible content per screen vs `html_simple`. A 90-min episode's full notes typically fit in 2-3 desktop screens of scrolling.

## When to use which

| Goal | Format |
|---|---|
| Save to Obsidian / note vault | `md` |
| Read on phone in bed | `html_simple` |
| Active study at desk | `html_dashboard` ⭐ |
| Share with someone else | `html_simple` (audio playback) or `md` (text only) |
| Quote-mining old episodes | `html_dashboard` (search box) |

## Rendering all three

You can render the same prep+outline+insights as multiple formats:

```bash
python scripts/render.py --format md --output notes.md ...
python scripts/render.py --format html_dashboard --output notes_dash.html ...
```

The pass1/pass2 LLM work doesn't need to repeat — it's already saved in `outline.json` and `insights.md`.

## Audio path notes

The HTML formats embed an `<audio>` element pointing at the local audio file. By default `render.py` writes a relative path (so the HTML + audio are portable as a pair).

If you want to share the HTML standalone:
1. Bundle the `audio.m4a` file with the HTML in the same folder
2. Or upload audio to a public URL and edit the `<source src="...">` line in the HTML

Future improvement: a `--audio-url` flag to substitute a public URL at render time. For now, copy the audio file alongside the HTML if sharing.
