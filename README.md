# Fantasy Draft Assistant

A live draft assistant for Sleeper fantasy football. It combines Sleeper's API (league/draft
settings, live picks, player pool) with a [FantasyPros](https://www.fantasypros.com/nfl/rankings/)
rankings CSV export to recommend the best available player for your team's biggest need, updated
in real time as the draft happens.

It uses **value-based drafting (VBD)**: instead of just going by raw rank, it scores each
available player by how far above "replacement level" they are at their position, and boosts
players who fill one of your still-open starting slots. It also warns you when the best remaining
player at a position you still need is about to fall out of the top recommendations, so thin
positions don't sneak up on you.

## Requirements

Python 3, standard library only - no dependencies to install.

## Usage

1. Export rankings from FantasyPros as a CSV (Rank + Player Name columns are required).
2. Find your draft's `draft_id` - it's in the URL when viewing the draft on Sleeper
   (`sleeper.com/draft/nfl/<draft_id>`), and works for both real and mock drafts.
3. Run:

```bash
python3 draft_assistant.py --draft-id <draft_id> --username <your_sleeper_username> \
    --rankings-csv <path_to_rankings.csv>
```

Leave it running in a terminal during your draft; it polls Sleeper every few seconds (`--poll-seconds`,
default 2) and prints updated recommendations whenever a pick happens or you're on the clock.

The first run downloads and caches Sleeper's full player list (`players_cache.json`, refreshed
every 24h) so subsequent runs start faster.
