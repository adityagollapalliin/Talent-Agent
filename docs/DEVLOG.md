# DEVLOG

Newest at the top. One entry per meaningful step. Move older months to `docs/devlog/DEVLOG-YYYY-MM.md`.

## 2026-11-02 — Step 1: skeleton, config loading, AGENTS.md, docs stubs

- **Did:** `uv` project (`pyproject.toml`, Python ≥3.12), package layout, `config.yaml` (confirmed by owner),
  `skillpulse/config.py` (Pydantic `Settings`, `Secrets` from `.env`, `Paths`), JSON logging util,
  `.env.example`, `.gitignore`, `AGENTS.md`, stubs for every `docs/*.md`.
- **Decisions:**
  - Model names: OpenAI `gpt-6-luna` (extraction) / `gpt-6.1-sol` (summary). They come from the `ChatModel` literal in
    `openai` SDK 3.42.1, because developers.openai.com is not reachable from the build sandbox. Prices are
    from third-party pricing pages, **so verify them**. Anthropic: `claude-haiku-5-5` / `claude-opus-5-5` (from the Anthropic docs).
  - Pricing lives in `config.yaml` so the cost guard needs no code change when prices move.
  - Added `region.india_locations` (city list used to filter global ATS boards), `summary_reserve_usd`,
    `sources`, `cache`, `report` blocks. All of them are config, not code.
  - Dry runs write to `data/dry-run/` and `reports/dry-run/` so fixtures never pollute real history.
- **Next:** SQLite store + dedupe.
