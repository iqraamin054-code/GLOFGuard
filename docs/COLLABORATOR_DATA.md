# Collaboration data layout

This branch is intended for collaborators who need code and reproducible inputs
without receiving credentials or large local database archives.

| Resource | Location | Purpose | Shared through Git |
|---|---|---|---|
| Training/feature data | `data/raw`, `data/processed`, `data/final` | Model development | Yes |
| Current lake map | `data/map/lakes_map.geojson` | Mapping/UI reference | Yes |
| PMD 2013 archive | `data/Glacial lakes_2013` | Authoritative historical source | Yes |
| Baseline input | `data/baseline_susceptibility.csv` | Reviewed migration input | Yes |
| Operational records | Supabase | Shared backend state | No database export in Git |
| SQLite databases/backups | Local ignored paths | Migration recovery only | No |

## Setup

1. Clone the collaboration branch.
2. Copy `.env.example` to a local `.env` and obtain backend credentials through
   an approved private channel. Do not commit `.env`.
3. Install the project dependencies and run the test suite.
4. Use the Supabase migration review and migration scripts only with an approved
   manifest. Do not treat Git data files as a replacement for the remote
   operational database.

The remote preservation migration is partial as of 2026-09-25: operational and
PMD reference rows are present remotely; the 8,806 baseline rows still require
the final verified transfer phase. This branch does not claim that phase is
complete.
