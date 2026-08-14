# F1 Plugin — Setup

## API keys

**None.** Both data sources (OpenF1 and Jolpica-F1) are free and open. Enable
the plugin and it works.

## 1. Install

### From a public git repository

Push this folder to a **public** GitHub repository named
`fiestaboard-plugin--f1`, then either paste the URL into **Integrations →
Install Plugin from Git** in the web UI, or call the API directly:

```bash
curl -X POST http://localhost:4420/api/plugins/install \
  -H "Content-Type: application/json" \
  -d '{"repository": "https://github.com/YOUR-USERNAME/fiestaboard-plugin--f1"}'
```

Only HTTPS URLs are accepted. FiestaBoard clones it into `external_plugins/`
and loads it automatically — no restart needed.

If you run FiestaBoard on a Raspberry Pi, run that command from the Pi (or
point it at the Pi's address instead of `localhost`).

### From a private repository

FiestaBoard clones anonymously and has no way to pass credentials, so a
private repo **cannot** be installed through the Git dialog or the API — it
will fail to find the repository. Copy the folder into FiestaBoard's
`external_plugins/` directory by hand instead and restart.

Under the **Home Assistant add-on**, FiestaBoard's `/app/data` is mounted from
HA's persistent `addon_config` volume, so that directory lives beneath
`/addon_configs/<slug>_fiestaboard/`. Reach it with the *Samba share*, *File
editor* or *Advanced SSH & Web Terminal* add-on. Installing any public plugin
once will show you the exact subdirectory external plugins land in.

Keep the folder named `fiestaboard-plugin--f1` — the plugin id (`f1`) is
derived from it. Manual installs don't auto-update; re-copy the folder to
upgrade.

## 2. Enable and configure

Open **http://localhost:4420 → Integrations → Formula 1** and switch it on.

| Setting | Default | What it does |
| ------- | ------- | ------------ |
| **Board Size** | `note` | `note` = 15×3, `flagship` = 22×6. Sets the width the pre-built lines are laid out for. |
| **Display Mode** | `auto` | `auto` shows live timing during a session and the fallback mode otherwise. Or pin it to `live`, `countdown`, `drivers`, `constructors`. |
| **Fallback Mode** | `countdown` | What `auto` shows between sessions. |
| **Timezone** | `Australia/Sydney` | Used for session start times and countdowns. Set this to your own zone. |
| **Include Practice Sessions** | on | Turn off if you only care about Qualifying, Sprint and Race. |
| **Live Window (minutes)** | 15 | How long before/after a session to keep showing live timing. |
| **Live Refresh (seconds)** | 20 | Polling rate during a session. Please don't go below 10. |
| **Standings Refresh (seconds)** | 1800 | How often championship standings refresh. |
| **Plugin Refresh Interval** | 30 | How often FiestaBoard asks the plugin for data. |

Verify it's working:

```bash
curl http://localhost:4420/api/plugins/f1/data
```

## 3. Put it on the board

### The easiest way

The plugin ships a ready-made demo page for both board sizes. On the plugin's
page in **Integrations**, use the demo to drop a working F1 page straight into
your Pages list, then edit it from there.

### The easy way

The plugin pre-formats a complete board for you. In the Page Editor, create a
page containing just:

```
{{f1.line1}}
{{f1.line2}}
{{f1.line3}}
```

(Add `{{f1.line4}}`–`{{f1.line6}}` on a Flagship.) That single page switches
itself between live timing and the countdown as the weekend progresses.

**Note (15×3)**

```
+---------------+   +---------------+   +---------------+
|NED RACE L34/72|   |NEXT ZANDVOORT |   |1 ANT       219|
|1ANT 2HAM 3NOR |   |RACE IN 1D 5H  |   |2 HAM       169|
|GAP +1.2 +2M   |   |SUN 23 AUG 2300|   |3 RUS     160.5|
+---------------+   +---------------+   +---------------+
     live               countdown        drivers/constructors
```

**Flagship (22×6)**

```
+----------------------+   +----------------------+
|NED RACE        L34/72|   |NEXT UP            NED|
|1 ANT         LEADER H|   |ZANDVOORT             |
|2 HAM FERRARI   +1.2 S|   |RACE             1D 5H|
|3 NOR MCLAREN      +2M|   |SUN 23 AUG 2300       |
|4 RUS MERCEDES  +8.1 M|   |WDC            ANT 219|
|5 LEC FERRARI  +12.4 H|   |WCC       MERCEDES 379|
+----------------------+   +----------------------+
```

### Building your own page

Every value is also available individually, pre-truncated to fit. In the Page
Editor's **Variables** panel they're grouped under Session, Live Timing, Next
Session, Championship and Display, each with a description and example value.

**Session and live timing**

| Variable | Example | Notes |
| -------- | ------- | ----- |
| `status` | `LIVE` | `LIVE`, `SOON`, or `OFF` |
| `mode` | `LIVE` | Which layout the pre-built lines are using |
| `session_short` | `RACE` | `FP1`, `QUALI`, `SQUALI`, `SPRINT`, `RACE` |
| `session_name` | `RACE` | Full name from OpenF1 |
| `circuit` / `country` | `ZANDVOORT` / `NED` | |
| `lap` | `L34/72` | Drops the total where it isn't known |
| `lap_current` / `lap_total` | `34` / `72` | |
| `flag` | `YELLOW` | `GREEN`, `YELLOW`, `RED`, `SAFETY CAR`, `VSC`, `CHEQUERED` |
| `leader` / `leader_name` / `leader_team` | `ANT` / `ANTONELLI` / `MERCEDES` | |
| `p1` `p2` `p3` | `ANT` `HAM` `NOR` | Current top three |
| `gap_p2` / `gap_p3` | `+1.2` | `LEADER`, `+1.2`, `+2M`, `DNF` |

**Next session**

| Variable | Example |
| -------- | ------- |
| `next_circuit` / `next_country` | `ZANDVOORT` / `NED` |
| `next_session` | `RACE` |
| `next_local_date` / `next_local_time` | `SUN 23 AUG` / `2300` |
| `countdown` | `1D 5H` |
| `countdown_days` / `countdown_hours` / `countdown_minutes` | `1` / `5` / `12` |

**Championship**

| Variable | Example |
| -------- | ------- |
| `season` / `round` | `2026` / `11` |
| `wdc_leader` / `wdc_leader_name` / `wdc_team` / `wdc_points` | `ANT` / `ANTONELLI` / `MERCEDES` / `219` |
| `wdc_gap` | `50` (P1's lead over P2) |
| `wcc_leader` / `wcc_points` / `wcc_gap` | `MERCEDES` / `379` / `72` |

**Lists** — index from 0:

- `{{f1.live.0.code}}`, `.position`, `.number`, `.name`, `.team`, `.gap`,
  `.interval`, `.tyre` (`S`/`M`/`H`/`I`/`W`), `.tyre_age`, `.line`
- `{{f1.drivers.0.code}}`, `.name`, `.team`, `.points`, `.wins`, `.gap`, `.line`
- `{{f1.constructors.0.short}}`, `.name`, `.points`, `.wins`, `.gap`, `.line`

Each list item also has a `.line` that's already laid out to 22 columns.

**Example custom page** — a Note showing your favourite driver's race:

```
{{f1.circuit}} {{f1.lap}}
P{{f1.live.0.position}} {{f1.live.0.code}} {{f1.live.0.tyre}}
{{f1.live.0.gap}}
```

## 4. Scheduling

Live timing is only interesting while cars are on track. A good pattern in
**Schedule Mode**:

- an F1 page during race windows in your timezone
- your normal pages the rest of the week

With **Display Mode = auto** you don't strictly need this — the same page turns
into a countdown on its own — but scheduling avoids giving F1 a permanent slot
in your rotation.

## Troubleshooting

**Board shows the countdown during a session.** The plugin only treats a
session as live within `live_window_minutes` of its scheduled times. If a race
is delayed, raise that value. Also confirm your Timezone setting — a wrong
timezone won't shift the countdown (times come through as UTC and are
converted), but it will show session start times in the wrong local time.

**Blank or dashed values during a session.** OpenF1 populates some feeds a
little after a session starts. Gaps and tyres typically appear within the first
few laps.

**`lap` shows `L34` with no total.** That circuit isn't in the `RACE_LAPS`
table in `__init__.py` — usually a brand-new venue. Add it and the total
appears.

**Standings show last season.** Expected before the first race of a new
season: current-year standings don't exist yet, so the plugin shows the last
completed championship rather than an empty board. Check `{{f1.season}}` to see
which year is displayed.

**Plugin unavailable / errors in the log.** Both APIs are community-run and
occasionally go down. The plugin degrades rather than failing — if only one
source is down you'll still get the other. Check:

```bash
curl "https://api.openf1.org/v1/sessions?year=2026"
curl "https://api.jolpi.ca/ergast/f1/current/driverstandings/?limit=3"
```

**Rate limiting.** If you see HTTP 429 in the logs, raise
`live_refresh_seconds`. The 20s default is well within both services' limits;
running several boards from one host is the usual cause.
