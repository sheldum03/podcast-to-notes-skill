# Chunking Long Episodes (>2h)

When `prep.json` contains multiple chunks, the agent must run pass 1 per chunk, merge, then run pass 2 over the full transcript (or chunked again if too large).

## Why ~2h is the threshold

DeepSeek V4, Claude, Gemini 2.5 — all advertise 1M token context, but quality degrades past ~150-200K tokens. At default settings:

- 1h podcast ≈ 25K tokens of transcript
- 2h podcast ≈ 50K tokens
- 3h+ podcast ≈ 75K+ tokens (Lex Fridman territory)

The skill's threshold is **60K tokens** (set in `prepare.py` as `LONG_THRESHOLD_TOKENS`). Above this, transcripts auto-chunk into ~30-45 minute pieces with 90-second overlap.

## Pass 1 per chunk

For each chunk in `prep["chunks"]`:

1. Read the chunk's transcript file (`chunk["transcript_path"]`)
2. Apply PASS1 prompt with `{chunk_info}` set to: `"chunk {index+1} of {total}, covers {start} – {end}"`
3. Save output JSON to `{work_dir}/chunks/chunk_{N:02d}_outline.json`

Each chunk is independent — the model only sees that chunk's transcript. The 90-second overlap ensures no insight is lost across boundaries.

## Merging chunk outlines

After all chunks have produced JSON outlines, merge them into a single `outline.json`:

### Merge rules

**`tldr`**: write a NEW 3-sentence TL;DR covering the entire episode. Don't concatenate the chunk TL;DRs (they'd be repetitive).

**`outline`**: concatenate all chunks' outline arrays in order. Then deduplicate: if two consecutive sections have very similar titles (overlap zone artifact), keep the one with more `key_points`.

**`mindmap`**:
- Use the FIRST chunk's `root` (or write a unified one if chunks disagree)
- Concatenate all `branches` from all chunks
- Deduplicate branches with same `label` — merge their children
- If total branches exceed 8, group similar ones under a parent (the model can do this in a final merge pass)

**`key_terms`**: union all terms, deduplicate by `term` field. Keep the longest `explanation` when duplicates exist.

### Merge prompt (recommended approach)

Rather than doing the merge logic in code, you can ask your model to do it:

```
# Task
Merge these N chunk outlines from a single podcast into one unified outline.json.

Rules:
- Write ONE unified 3-sentence TL;DR (not concatenated chunk TL;DRs)
- Concatenate outline sections in time order; merge sections with very similar titles
- Mind map: unify root, merge branches with same label, keep total branches ≤ 8
- key_terms: union, deduplicate, keep best explanation

Output JSON with the same schema as a single chunk's outline.

# Chunk outlines (in time order)
{chunk_outlines_concatenated}
```

This works well because chunk outlines are small (~3K tokens each, ~12K for 4 chunks), well under any model's effective context.

## Pass 2 strategies for long episodes

### Strategy A: Full transcript (preferred when it fits)

If the full transcript ≤ 200K tokens (typical 4-6 hour episode), run pass 2 once with the full transcript. Models can handle this; it produces the highest quality because the model sees everything at once.

### Strategy B: Chunked extraction + global selection

For episodes > 4-5 hours where the full transcript pushes context limits:

**Step B1**: Run a modified PASS2 on each chunk asking for **candidates** (looser filter):

```
[Same PASS2 prompt as before, but replace the quote count target:]
- Generate 8-15 candidate quotes per chunk (not the usual 5-10 — we'll filter later)
- All candidates clearly marked as "from chunk N"
```

**Step B2**: Selection pass:

```
# Task
You have candidate quotes/contrarian/hooks from N chunks of one podcast.
Select FINAL: 8-12 quotes, all real contrarian takes, 5-7 hooks total.
Same filter rules as before. De-duplicate. Output final Chinese Markdown.

# Candidates from each chunk
{all_chunks_candidates}
```

This 2-step approach typically uses ~40K tokens per chunk + ~20K for selection — much smaller than fitting the full 250K+ transcript.

### Strategy C: When even chunks are too big

Very rare — would mean a single 90-min chunk exceeds your model's working context. If this happens, reduce `CHUNK_TARGET_TOKENS` in `prepare.py` from 25_000 to 15_000 and rerun `prepare.py` (delete `prep.json` first to force regeneration).

## Implementation hints

- Save chunk outlines to disk immediately after generating each one (don't keep in memory)
- If pass 1 fails on chunk N, you can retry just that chunk — others are already saved
- The merge step is cheap; redo it if you want to tweak the unified outline
- Keep all intermediate files in `{work_dir}/chunks/` — they're small and useful for debugging
