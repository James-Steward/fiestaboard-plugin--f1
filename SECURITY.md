# Security

## Threat model

This plugin runs server-side inside FiestaBoard, on a device on your home
network. It holds no credentials, writes no files, spawns no processes, and
opens no listening sockets. It makes outbound HTTPS requests to two fixed
hosts and returns strings that FiestaBoard renders on a split-flap display.

The realistic attack surface is therefore **the API responses**, and secondarily
the plugin's own configuration.

| Asset | Exposure |
| ----- | -------- |
| Credentials | None exist. Both APIs are unauthenticated; the board's own key belongs to FiestaBoard, not this plugin. |
| Filesystem | Never read or written. |
| Command execution | No `subprocess`, `eval`, `exec`, `pickle`, or dynamic import. |
| Network | Outbound only, to two hard-coded HTTPS hosts. No URL component is user- or response-controlled. |
| Board output | Every string is filtered to the Vestaboard character set and length-capped. |

## Untrusted input

Both upstreams are community-run and unauthenticated, so **every response
field is treated as untrusted**, regardless of its documented type:

- **Type confusion.** Any field may arrive as a dict, list, null or NaN.
  `_text()` coerces scalars and rejects containers before a value is used as a
  dictionary key or has string methods called on it. `_as_int()` returns
  `None` rather than raising. One unusable row is dropped; it never takes the
  display offline.
- **Board control codes.** Vestaboard colour tiles are written `{66}`.
  `{` and `}` are not board characters, so `sanitize()` replaces them with
  spaces — upstream data cannot inject coloured tiles or forge layout.
  Colour codes are preserved *only* in rows this plugin composes itself.
- **Python repr leakage.** `sanitize()` renders containers as `""` rather than
  `{'evil': 1}`, so upstream structure never reaches the board.
- **Resource exhaustion.** Responses are streamed and abandoned past
  `MAX_RESPONSE_BYTES` (8 MB), and both a declared `Content-Length` and the
  actual byte count are checked. Row counts are capped at `MAX_ROWS`. The
  cache is bounded to `MAX_CACHE_ENTRIES`, evicting oldest-first — its keys
  include session ids, so an unbounded cache would grow all season.
- **Slow responses.** Every request has a 12-second timeout.

## Configuration

`timezone` is the only free-text setting. It is passed to `zoneinfo.ZoneInfo`,
which rejects absolute paths and `..` traversal; an invalid value falls back to
UTC. Numeric settings are range-checked in `validate_config()` and re-clamped
at use, so a value written directly into `settings.json` cannot cause a divide
by zero or a negative interval.

## What this plugin cannot protect you from

If OpenF1 or Jolpica-F1 were compromised, an attacker could **choose the text
on your board** — within the character set and field widths. They could not
execute code, read files, or reach other hosts on your network. Data is
cosmetic; there is nothing to escalate to.

`fiestaboard_auth_enabled` and network exposure of port 4420 are FiestaBoard
concerns, not this plugin's.

## Verification

`tests/test_plugin.py::TestUntrustedUpstream` covers colour-code injection,
container-typed fields, unhashable dictionary keys, malformed standings,
non-JSON bodies, oversized responses, row-count caps, cache bounds and
timezone traversal.

## Reporting

Open an issue on the repository. There is no sensitive data at stake, so
please report in the open — it is more useful to other users that way.
