# Literature Workflow README

Generated: 2026-07-06

## Run

```powershell
python literature_search.py
```

The script uses only Python standard-library HTTP utilities plus CSV/JSON writers. It queries Crossref, OpenAlex, and Semantic Scholar, then verifies every DOI through Crossref.

## Outputs

- `literature_results_verified.csv`: rows with `verified_core` or `supplementary_background`.
- `literature_results_all.json`: all candidates, including excluded or pending records.
- `excluded_or_pending.csv`: records that failed DOI, journal, year, or relevance checks.
- `literature_review_report.md`: Chinese report with method, tables, gaps, contribution framing, and claim-literature mapping.
- `references_gbt7714.md`: GB/T 7714 style reference draft.
- `references_apa.md`: APA style reference draft.

## How To Review

1. Open `literature_results_verified.csv`.
2. For every `verified_core` row, click `doi_url` and confirm title, authors, year, journal, volume/issue/pages.
3. Read the full text before using `main_findings`; the script intentionally leaves findings as metadata-only pending text.
4. Move useful non-core papers into the background section only if they support methods such as MARL, self-play, SUMO, ALINEA, or traffic-flow theory.

## Updating Keywords

Edit `SEARCH_QUERIES` in `literature_search.py`. Keep query groups crossed, such as:

- `"variable speed limit" "reinforcement learning"`
- `"ramp metering" "reinforcement learning"`
- `"self-play" "multi-agent reinforcement learning" traffic`
- `"traffic safety" "variable speed limit"`

Do not add papers to `verified_core` manually unless DOI and journal metadata have been checked.
