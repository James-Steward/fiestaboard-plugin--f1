# FiestaBoard F1 Plugin

Real-time Formula 1 data for Vestaboard and other split-flap displays running
[FiestaBoard](https://fiestaboard.app).

Live timing during a session, a countdown to the next one when nothing is
running, and driver/constructor championship standings — all from free public
APIs. **No API key, no account, no paid tier.**

```
+---------------+   +---------------+   +---------------+
|NED RACE L34/72|   |NEXT ZANDVOORT |   |1 ANT       219|
|1ANT 2HAM 3NOR |   |RACE    13H 45M|   |2 HAM       169|
|GAP +1.2 +2M   |   |SUN 23/08 23:00|   |3 RUS     160.5|
+---------------+   +---------------+   +---------------+
     live               countdown           standings
```

See [`docs/SETUP.md`](docs/SETUP.md) for installation and configuration.

## Data sources

| Source | Used for | Auth | Notes |
| ------ | -------- | ---- | ----- |
| [OpenF1](https://openf1.org) | Session calendar, live positions, gaps, tyres, lap count, flags | None | Real-time feed, a few seconds behind the broadcast |
| [Jolpica-F1](https://github.com/jolpica/jolpica-f1) | Driver and constructor championship standings | None | Community-maintained successor to the retired Ergast API |

Neither service requires registration. Both are community-run, so the plugin
treats every call as best-effort: if standings are unreachable the live board
still renders, and vice versa.

Each stage is isolated by a broad `except Exception`, not just
`requests.RequestException`. That distinction matters: an overloaded upstream
returning a truncated body or a CDN error page with a 200 status raises
`ValueError`, not a network error. Catching only network errors let that
escape and marked the whole plugin unavailable, blanking even the countdown —
which needs no live data at all. The plugin now reports `available=False` only
when the calendar *and* the standings are both gone.

## How it works

`fetch_data()` runs three loosely-coupled stages, each with its own cache TTL,
and merges the results:

1. **Calendar** — `GET /v1/sessions?year=YYYY`, cached 6 hours. Cancelled
   sessions are dropped, times are parsed to UTC, and the list is sorted. From
   it the plugin derives the *live* session (now within
   `[start - live_window, end + live_window]`), the *next* session, and the
   *next race*. In late December the following year's calendar is appended so
   the countdown keeps working over the winter break.

2. **Live timing** (only when a session is live), cached at
   `live_refresh_seconds`:

   | Endpoint | Purpose |
   | -------- | ------- |
   | `/v1/drivers` | Number → acronym/name/team, cached 1 hour per session |
   | `/v1/position` | Latest classification — whole session, latest row per driver |
   | `/v1/intervals` | `gap_to_leader` and `interval`, last 5 minutes |
   | `/v1/stints` | Current tyre compound and age (highest `stint_number` wins) |
   | `/v1/laps` | Leader's lap count → current lap number |
   | `/v1/race_control` | Track-wide flag and safety car state, cached 30s |
   | `/v1/session_result` | Fallback classification once the session ends |

   Only the most recent row per driver is kept from `position` and `intervals`.
   Once the session has ended the plugin switches to `session_result`, which
   carries final gaps and DNF/DNS/DSQ.

   **`/position` is a change log, not a feed.** It emits a row only when a
   driver's position actually changes, so a settled race leaves 15–20 minute
   gaps, and a driver who led from the grid may have no row for an hour.
   Querying it with a rolling time window therefore returns nothing during
   calm phases — dropping the board out of live mode mid-race — and can miss
   the leader entirely. The whole session is fetched instead; even a full
   grand prix is only a few hundred rows.

3. **Standings** — `GET /ergast/f1/{year}/driverstandings/` and
   `constructorstandings/`, cached at `standings_refresh_seconds` (30 min by
   default). Before the first race of a season both come back empty, so the
   plugin automatically falls back to the previous year's final standings
   rather than showing a blank board.

### Total lap counts

OpenF1 does not publish a race's scheduled distance, so `RACE_LAPS` maps
`circuit_short_name` to a lap count for the established circuits. A missing
entry degrades gracefully: the board shows `L34` instead of `L34/72`. New or
unconfirmed venues are deliberately omitted rather than guessed.

## Board formatting

Everything written to the board passes through `sanitize()`, which:

- strips diacritics (`Montréal` → `MONTREAL`, `São Paulo` → `SAO PAULO`)
- uppercases (split-flap boards have no lowercase)
- replaces anything outside the Vestaboard character set with a space

`sanitize(collapse=False)` is used for already-laid-out rows so that the
padding doing the column alignment survives. `pad_row()` left/right-justifies
two fragments within the board width and truncates the left fragment when they
would collide.

### Colour tiles

The standings pages emit Vestaboard colour codes (`{66}` and friends) for team
blocks. A colour code is four characters of template text but exactly **one
tile**, so anything that measures width has to count tiles, not characters —
that's what `tiles()` is for, and why `fit()` and `pad_row()` both work in
tiles. `_fold()` deliberately runs only over the non-colour segments so the
braces survive sanitising.

`swatch(team, count)` returns a run of tiles cycling the team's colours, or an
empty string when the team is unmapped or there's no room, in which case the
row falls back to plain right-aligned text. Blocks are capped at
`MAX_SWATCH_TILES` so a short team name doesn't produce a much wider block than
a long one on the same page.

The consequence for `manifest.json` is that `line1`…`line6` declare a
`max_length` in *characters* (40) that exceeds the board's tile count — the
colour codes account for the difference.

### Layout

The plugin renders `line1`…`line6` (and `formatted_lines`) for the configured
board — 3 rows × 15 columns for a Note, 6 × 22 for a Flagship — in one of four
modes. On a Flagship, a team name is dropped whole rather than sliced mid-word
when a live timing row would otherwise overflow.

If you'd rather design your own page, ignore the pre-built lines entirely and
compose from the individual variables; they are all pre-truncated to the
`max_lengths` declared in `manifest.json`.

## Treating upstream data as untrusted

Both APIs are unauthenticated and community-run, so every response field is
handled as untrusted input regardless of its documented type. `_text()`
coerces scalars and rejects containers before a value is used as a dictionary
key or has string methods called on it; `_as_int()` returns `None` instead of
raising. A single unusable row is dropped rather than taking the whole display
offline.

Responses are streamed and abandoned past `MAX_RESPONSE_BYTES`, row counts are
capped at `MAX_ROWS`, and the cache is bounded by `MAX_CACHE_ENTRIES` — its
keys include session ids, so it would otherwise grow all season.

Because `{` and `}` are not board characters, `sanitize()` strips them, and
upstream data cannot inject colour tiles. Colour codes survive only in rows
this plugin composes itself.

See [SECURITY.md](SECURITY.md) for the full threat model.

## A note on the manifest format

`manifest.json` targets the format used by the plugins FiestaBoard ships
today, **not** the one in the published Plugin Development Guide. The guide is
still at version 6.1 while the app is on 8.x, and the two differ:

| | Published guide (6.1) | Shipping plugins (8.x) |
| --- | --- | --- |
| `variables.simple` | array of names | object keyed by name, each with `description`, `type`, `max_length`, `group`, `example` |
| `variables.groups` | — | groups variables in the editor's picker |
| `max_lengths` | every variable | array wildcards only; simple vars carry `max_length` inline |
| `previews` / `teaser` | — | board mock-ups on the plugin card |
| `demo` | — | one-click starter page per board size |
| `min_refresh_seconds`, `fiestaboard_version` | — | polling floor and compatibility range |

If you're writing another plugin, read a current manifest from the registry
(e.g. [`fiestaboard-plugin--stocks`](https://github.com/Fiestaboard/fiestaboard-plugin--stocks/blob/main/manifest.json))
rather than following the guide alone.

The test suite enforces the parts a validator won't catch: previews fit their
board's tile count, the teaser fits a Note, demo templates only reference
variables that exist, every variable declares a real group, and each example
respects its own `max_length`.

## Replaying a past session

Live timing can normally only be exercised while cars are on track.
`tools/replay.py` downloads a finished session and steps a simulated clock
through it, printing what the board would have shown:

```bash
python3 tools/replay.py                    # last completed race
python3 tools/replay.py --list 2026        # find session keys
python3 tools/replay.py --session 11353 --step 5 --board flagship
```

Rows are marked when the board content changes, so it doubles as a check on
how often the display would physically flap.

## Development

```bash
# Inside the FiestaBoard repo
docker-compose exec fiestaboard pytest external_plugins/fiestaboard-plugin--f1/tests/ -v \
  --cov=external_plugins/fiestaboard-plugin--f1 --cov-report=term-missing

docker-compose exec fiestaboard python scripts/validate_plugins.py --verbose

curl http://localhost:4420/api/plugins/f1/data
```

The test suite installs a stand-in for `src.plugins.base` when it can't be
imported, so `pytest tests/` also works on a standalone checkout.

## Rate limits and etiquette

Both APIs are volunteer-funded. Defaults are chosen to be polite:

- live polling every 20s, and **only while a session is actually running**
- standings every 30 minutes
- the calendar every 6 hours

Please don't lower `live_refresh_seconds` below 10.

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with, endorsed by, or connected to Formula 1, the FIA, or
Vestaboard. F1 and Formula 1 are trademarks of Formula One Licensing BV.
