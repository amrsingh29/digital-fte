# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-17

### Added

- **Hermes and omp:** read the sessions of two more coding agents, Hermes (`~/.hermes/state.db`)
  and omp (`~/.omp/agent/sessions`). Both are on by default, with no monthly cost.
- **`fte mcp`:** a read-only MCP server so your own agents (Claude Code, opencode,
  Goose) can ask about your Digital FTE, with three tools: `dfte_summary`,
  `dfte_tasks` and `dfte_review_queue`. Four switches under `[mcp.profiles.me]` in
  `config.toml` choose what they may see (`totals`, `task_details`, `names`,
  `content`); only totals are on to begin with. `fte mcp --dry-run TOOL` shows what a
  tool would return, and `~/.digital-fte/mcp-audit.log` records every question by
  field name. See [the MCP server](docs/mcp-server.md).
- **Data and privacy page:** what the app reads, keeps and sends, in four levels
  ([docs/data-and-privacy.md](docs/data-and-privacy.md)).

### Changed

- **AI is off by default.** A new install uses rules only and sends nothing to an AI service
  until you choose one in `config.toml` or the dashboard's Settings. Existing config files are
  not changed; an older file that names a provider keeps it.
- The internal endpoint in the default config is replaced with a placeholder.

## [0.1.1] - 2026-10-28

### Fixed

- Default dashboard port is now **8770**. Port 8765 clashed with other apps that
  commonly run locally, so `fte serve` could fail with *Address already in use*.
  `--port` still overrides it. README, the guides, the troubleshooting table and
  the editor launch config all follow the new default.

## [0.1.0] - 2026-10-15

First release.

### Added

- Reads Claude Code, opencode, Cursor and Cline history on your Mac (read-only).
- Splits sessions into tasks; estimates hours from evidence, refined by an AI
  service of your choice (Cortex, Claude, OpenAI, Gemini or a local model).
- Monthly Digital FTE with likely range, leverage, cost per DFTE and keep rate.
- Purposes: project work, general & learning, setup & admin.
- Virtual team: one or two job roles per task, role costs, over-time chart.
- Task editor with sizes, roles and learning from your corrections.
- Daily refresh via launchd that catches up after sleep or shutdown.
- Totals-only summary page to share; troubleshooting log (`app.log`).
- MIT licence.

[Unreleased]: https://github.com/amrsingh29/digital-fte/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/amrsingh29/digital-fte/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/amrsingh29/digital-fte/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/amrsingh29/digital-fte/releases/tag/v0.1.0
