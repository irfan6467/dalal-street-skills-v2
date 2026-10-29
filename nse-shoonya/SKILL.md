---
name: nse-shoonya
description: >-
  Shoonya (Finvasia) live data for every NSE/BSE skill. Logs in with Shoonya's
  OAuth flow in the user's own terminal (secrets from environment variables,
  never typed into chat), saves a one-day session, and switches every other
  skill from free delayed Yahoo bars to Shoonya's exchange data: 1/3/5/10/15/
  30/60/120/240-minute bars, daily bars, quotes with the circuit band. Runs a
  live read-only selftest that confirms the auth mode, the bar schema and that
  volume is per bar. Use when the user says "connect Shoonya", "use Shoonya
  data", "log in to Shoonya", "is my Shoonya session live", "switch to live
  data", "selftest", "Shoonya quote", or when another skill reports a
  Shoonya login or data error.
argument-hint: "login | status | use shoonya|yahoo | selftest | quote SYMBOL | bars SYMBOL"
---

# nse-shoonya

On Windows, use `py` in place of `python3`.

Every other skill reads prices through one function. This skill points that
function at Shoonya. Nothing else changes: the scanner, entry timer, exit
planner, time machine and risk check keep working the same way, on live
exchange data instead of Yahoo's free feed, which is unofficial and can lag.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/shoonya.py" status                  # session, provider, env vars set or not
python3 "${CLAUDE_SKILL_DIR}/shoonya.py" login                   # prints the OAuth URL, then asks for the redirect
python3 "${CLAUDE_SKILL_DIR}/shoonya.py" login --code '<redirect URL>'
python3 "${CLAUDE_SKILL_DIR}/shoonya.py" selftest                # live, read-only: run once per account
python3 "${CLAUDE_SKILL_DIR}/shoonya.py" use shoonya             # every skill now reads Shoonya
python3 "${CLAUDE_SKILL_DIR}/shoonya.py" use shoonya --allow-fallback   # Yahoo only if Shoonya fails
python3 "${CLAUDE_SKILL_DIR}/shoonya.py" use yahoo               # back to the free feed
python3 "${CLAUDE_SKILL_DIR}/shoonya.py" quote RELIANCE          # last, prev close, circuit band
python3 "${CLAUDE_SKILL_DIR}/shoonya.py" bars TCS --interval 5m --range 1d
```

## Rules for Claude

1. **Never ask for, accept, or repeat a password, TOTP, API secret or session
   token in chat.** If the user pastes one, tell them to revoke and regenerate
   it in Shoonya, and do not use it.
2. **Login runs in the user's terminal, not through Claude.** The OAuth page
   needs their password and TOTP. Tell them to set `SHOONYA_UID`,
   `SHOONYA_CLIENT_ID` and `SHOONYA_SECRET` in their shell, run `login`, open
   the URL, and paste the redirect address back into the terminal. Claude may
   run `login --code` only if the user explicitly asks and pastes the
   redirect URL (the code is single-use and expires in about 2 minutes; it is
   not a secret once used).
3. **Run `selftest` before `use shoonya`.** Shoonya marks its own response
   schemas as unverified. The selftest checks them on the user's account,
   finds which auth mode works (form `jKey` or `Authorization: Bearer`) and
   saves it. If it FAILS, leave the provider on Yahoo and show the issues.
4. **Never turn on `--allow-fallback` silently.** Without it, a Shoonya
   failure stops the skill with a clear error instead of quietly using
   delayed data. Offer fallback only for research, never for live entries.
5. This skill is read-only. It never places, modifies or cancels orders.
   Orders go through `/nse-broker` and its consent gates.

## Setup the user does once

1. In Shoonya, create an API app and note the app key (client id) and secret.
   Whitelist the IP of the machine that will run the skills.
2. Set the three environment variables in their shell profile.
3. `login`, then `selftest`, then `use shoonya`.

Sessions last one trading day. When another skill says "not logged in" or
"session is from <date>", the fix is `login` again.

## What it reads and writes

| file | what |
| --- | --- |
| env `SHOONYA_UID`, `SHOONYA_CLIENT_ID`, `SHOONYA_SECRET` | credentials, read at login only |
| `nse/shoonya_session.json` | the day's session token, owner-only permissions. Add `nse/` to `.gitignore` |
| `nse/config.json` → `data` | `provider` (`yahoo` default / `shoonya`), `allow_yahoo_fallback`, `auth` (`jkey` / `bearer`, set by selftest), last `selftest` report |
| `nse/cache/shoonya/` | the day's symbol master (token lookup) |
| env `NSE_DATA=shoonya\|yahoo` | overrides the config for one run |

## Limits, stated plainly

- Shoonya allows about 1 market-data request per second per instrument. A
  full Nifty 500 scan on Shoonya therefore takes minutes, not seconds. The
  feed throttles itself. For whole-market daily scans, the bhavcopy route in
  `/nse-scanner` is faster and is official exchange data too.
- Intraday history depth on Shoonya's TPSeries is shallow and undocumented.
  `/nse-timemachine`'s 60-day replay may come back shorter: it says so.
- Fundamentals (`/nse-analyst`) and FX stay on Yahoo: Shoonya has no such data.
- `2m` and `90m` bars do not exist on Shoonya; asking for them is an error
  (or Yahoo, with fallback on).
