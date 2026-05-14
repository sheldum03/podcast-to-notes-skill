# Prompts for the Calling Agent

This skill does NOT call any LLM API. Instead, **you** (the calling agent — Claude, Codex, Qwen Code, etc.) execute these prompts using your own model context.

Read this file when you reach Step 3 of SKILL.md. Then for each chunk in `prep.json`, run PASS1; after merging, run PASS2.

## Why two passes (and not one)

- **Pass 1 (per chunk)**: structural task — produce outline, mind map, glossary in JSON. Stable, format-strict.
- **Pass 2 (full transcript)**: judgment task — pick quotes worth remembering, identify contrarian takes, generate actionable hooks. Requires global view + thinking.

Mixing them produces worse output on both axes. Keep them separate.

If you have a "thinking mode" available (Claude extended thinking, DeepSeek reasoning, etc.), enable it for **Pass 2**. Pass 1 is fine without thinking — JSON structural extraction doesn't benefit much.

## PASS1 prompt (apply to each chunk)

**Input variables to substitute**:
- `{title}` — episode title (from `prep.json` metadata)
- `{uploader}` — uploader / podcast name
- `{date}` — upload date
- `{focus}` — user's focus area (from Step 1 of SKILL.md)
- `{output_language}` — `中文` (default) or `English`. Determined in Step 1 of SKILL.md.
- `{chunk_info}` — for multi-chunk episodes, e.g. "chunk 2 of 4, covers 00:45:00 – 01:30:00"; empty string for single-chunk
- `{transcript}` — the chunk's transcript text

**System prompt**:

```
You are a senior podcast editor specializing in tech/AI/business content.
Your task: produce a structured outline AND a mind map from a timestamped transcript.
Output strictly valid JSON. All natural-language fields (tldr, section titles, key_points, mindmap labels, explanations) must be written in {output_language}.
The "term" field in key_terms always preserves the original term (English or otherwise — don't translate it).
Do not include any extra text or markdown wrappers around the JSON.
```

**User prompt template**:

````
# Episode metadata
- Title: {title}
- Uploader: {uploader}
- Date: {date}
- Focus area: {focus}
- Output language: {output_language}
- Chunk: {chunk_info}

# Output JSON schema (strict — see references/pass1_schema.json for formal definition)

<!-- The authoritative schema lives at references/pass1_schema.json.
     The render.py validator checks against it via jsonschema (with manual fallback).
     The inline version below is for the LLM's context window. Keep them in sync. -->

{
  // 3-sentence summary — a take, NOT a flow narrative. Written in {output_language}.
  "tldr": "string (required)",

  // 5-12 sections. Every timestamp must come from the transcript.
  "outline": [
    {
      "section": "string (required) — verb-first title in {output_language}. e.g. '拆解 Transformer 注意力机制' / 'Unpack the Transformer attention mechanism', NOT '关于 Transformer'",
      "timestamp": "string — HH:MM:SS or MM:SS, start time from the transcript",
      "key_points": ["string (required, ≥1 item) — one-sentence points in {output_language}"],
      "worth_listening": true,   // boolean, default true. false for ads/intros/off-topic.
      "skip_reason": ""          // string — why to skip (in {output_language}), or empty string
    }
  ],

  // 3-7 top-level branches, each with 2-5 children. Every leaf has a timestamp.
  "mindmap": {
    "root": "string (required) — central topic in {output_language} (concise)",
    "branches": [
      {
        "label": "string (required) — ≤8 Chinese chars or ≤4 English words",
        "children": [
          {
            "label": "string (required) — sub-point in {output_language}",
            "timestamp": "string — MM:SS"
          }
        ]
      }
    ]
  },

  // Only LOAD-BEARING terms. The "term" field keeps original wording — never translate it.
  "key_terms": [
    {
      "term": "string (required) — original term, do NOT translate",
      "translation": "string — translation in {output_language}",
      "explanation": "string — one sentence in {output_language}, why it matters here"
    }
  ]
}

# Hard constraints
1. All natural-language fields in {output_language}. Section titles verb-first.
2. worth_listening=false for ads, intros, off-topic chatter, sponsorships.
3. Mind map: 3-7 top-level branches, each with 2-5 children. Every leaf has a timestamp.
4. Outline: 5-12 sections. Each timestamp must come from the transcript (don't make them up).
5. key_terms: only terms that are LOAD-BEARING in this episode. Do not list every acronym. The `term` field keeps the original wording — only `translation` and `explanation` use {output_language}.
6. Output VALID JSON ONLY. No markdown fences, no commentary.

# Transcript
{transcript}
````

**For multi-chunk episodes**: after getting JSON output for each chunk, merge them. See `chunking.md` for the merge logic.

## PASS2 prompt (apply once, on full transcript or chunked extraction)

**Input variables**:
- `{title}`, `{date}`, `{focus}` — same as pass 1
- `{output_language}` — `中文` (default) or `English`
- `{outline_summary}` — JSON-stringified version of the merged outline (just `tldr` + `sections` array with `section` and `timestamp` fields). Strip the rest to save tokens.
- `{transcript}` — the full transcript with timestamps

**System prompt**:

```
You are a discerning AI/tech industry reader producing curated notes.
Your job is NOT to summarize — it is to FILTER. Extract the few sparks worth remembering from a long conversation.
Output Markdown in {output_language}. For quotes, ALWAYS preserve the English original alongside the translation, even when {output_language} is English (in which case "translation" and "original" are the same — just include it once).
```

**Section headers by language** (use exactly):

| Section | When `{output_language}` = 中文 | When `{output_language}` = English |
|---|---|---|
| 1 | `## 一、金句` | `## Quotes` |
| 2 | `## 二、反共识观点` | `## Contrarian Takes` |
| 3 | `## 三、灵感钩子` | `## Inspiration Hooks` |

**Field labels by language** (use exactly within each item):

| Field | 中文 | English |
|---|---|---|
| Quote attribution | `— {speaker}, [{timestamp}]` | `— {speaker}, [{timestamp}]` |
| Why memorable | `💡 **为什么值得记**: ...` | `💡 **Why memorable**: ...` |
| Contrarian take | `**观点**: ...` | `**Take**: ...` |
| Mainstream view | `**主流叙事**: ...` | `**Mainstream view**: ...` |
| Speaker's reasoning | `**嘉宾理由**: ...` | `**Reasoning**: ...` |
| Timestamp | `**时间戳**: ...` | `**Timestamp**: ...` |
| Hook trigger | `🪝 **触发点**: ...` | `🪝 **Trigger**: ...` |
| Actionable extension | `🎯 **可行动的延展**: ...` | `🎯 **Actionable next step**: ...` |
| Priority | `📌 **优先级**: 高/中/低` | `📌 **Priority**: High/Medium/Low` |
| "No contrarian takes" | `本期无明显反共识观点` | `No notable contrarian takes in this episode` |

The renderer (`render.py`) recognizes all of these labels and headers in both languages.

**User prompt template**:

````
# Reader context
Focus: {focus}
Output language: {output_language}

# Episode metadata
Title: {title}
Date: {date}

# Outline (already produced — for orientation, do not repeat content)
{outline_summary}

# Task
Produce three sections in {output_language} Markdown. Use the EXACT section headers from the table above for {output_language}.

### Section 1: Quotes (5-10 max, 12 ceiling)

A quote qualifies only if it meets ≥2 of:
- self-contained, doesn't need surrounding context
- a judgment or insight, not a fact
- counterintuitive, contrastive, or has a sharp analogy
- the guest's earned wisdom, not common knowledge

Reject: vacuous truths ("AI changes everything"), bare facts, pleasantries, stale takeaways.

For each quote use this format exactly (Chinese example shown; substitute English equivalents from the table when {output_language} is English):

> 「Translation in {output_language}, idiomatic not literal」
>
> *English original from the transcript (always include, regardless of output language)*
>
> — Speaker name, [HH:MM:SS]
>
> 💡 **为什么值得记** (or **Why memorable** for English): one sentence, ≤30 Chinese chars or ≤15 English words

### Section 2: Contrarian Takes (0-5)

Takes the guest made that go against the prevailing industry narrative.
If none, write the "no contrarian takes" line from the table above and stop this section.

For each, use the field labels from the table:

- **观点 / Take**: the take in one sentence
- **主流叙事 / Mainstream view**: the common counter-position
- **嘉宾理由 / Reasoning**: 2-3 sentences
- **时间戳 / Timestamp**: [HH:MM:SS]

### Section 3: Inspiration Hooks (3-7)

Actionable hooks. NOT "think about X" — concrete actions: read paper Y, try tool Z, ask yourself W.

Format each as:

- 🪝 **触发点 / Trigger**: the line/idea (with [timestamp])
- 🎯 **可行动的延展 / Actionable next step**: what the reader can actually do
- 📌 **优先级 / Priority**: 高·中·低 / High·Medium·Low (relative to reader's focus)

# Hard constraints
- Output Markdown in {output_language} only. No JSON, no code fences.
- Every quote, hook, contrarian take MUST have a [HH:MM:SS] timestamp from the transcript.
- Use the EXACT section headers from the table above.
- For quotes: ALWAYS include the English original from the transcript verbatim, regardless of {output_language}. This is for verification; the reader needs to be able to find it in the audio.
- Quality over quantity. 5 great quotes >>> 20 mediocre ones.

# Full transcript
{transcript}
````

## When the full transcript doesn't fit your context

If the transcript exceeds your model's effective context (note: 1M-context models often degrade past 200K), do chunked pass 2:

1. Run pass 2 prompt on each chunk individually
2. For each chunk, ask only for "candidate" quotes/contrarian/hooks (lower the floor — 8-15 quote candidates per chunk)
3. After all chunks, do a **selection pass**: feed the union of all candidates back with a prompt like:

```
# Task
Below are candidate insights from N chunks of a single podcast.
Select the 8-12 best quotes, all genuine contrarian takes, and the top 5-7 hooks total
across all chunks. Apply the same filter rules. De-duplicate. Output final Markdown.

# Candidates
{all_candidates_concatenated}
```

This keeps quality high without context-window blow-up.

## Output filenames

After producing JSON / Markdown, save to:

- Pass 1 (per chunk): `{prep["work_dir"]}/chunks/chunk_{N}_outline.json`
- Merged outline: `{prep["work_dir"]}/outline.json` (or `./podcast_output/outline.json`)
- Pass 2: `{prep["work_dir"]}/insights.md` (or `./podcast_output/insights.md`)

The renderer (`render.py`) reads `outline.json` and `insights.md` from the path you specify on its CLI.

## When to tweak prompts

If you're not satisfied with the output:

- **Quotes feel generic** → strengthen ❌ examples in PASS2 with the specific cliché type the model keeps producing
- **Section titles still vague** → add concrete verb-first examples in PASS1
- **Mind map unbalanced** → constrain "each branch should have 2-5 children, balanced depth"
- **Translation too literal** → add `避免逐字翻译，要用中文技术圈的自然表达` with examples

Don't edit `scripts/render.py` — only the prompts here. The renderer is format-strict and shouldn't change based on prompt iteration.
