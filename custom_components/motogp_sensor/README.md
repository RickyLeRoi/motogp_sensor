# MotoGP Sensor for Home Assistant

[![Last commit](https://img.shields.io/github/last-commit/RickyLeRoi/motogp_sensor)](#)
[![Version](https://img.shields.io/github/v/release/RickyLeRoi/motogp_sensor)](#)
[![HA Community forum](https://img.shields.io/badge/Home%20Assistant-Community%20Forum-319fee?logo=home-assistant)](https://community.home-assistant.io/)

## Your home, in sync with MotoGP

**`MotoGP Sensor`** is a custom [Home Assistant](https://www.home-assistant.io/) integration that brings the full world of MotoGP into your smart home.

It combines live session timing with static season information, giving you everything from real-time rider positions, lap times, and pit stop data to race schedules, standings, and historical race results.

Whether you want your lights to react to the start of a race weekend or build dashboards that visualize the championship standings, `MotoGP Sensor` keeps your home perfectly in sync with MotoGP.

> This project is inspired by the excellent [F1 Sensor](https://github.com/Nicxe/f1_sensor) integration by [@Nicxe](https://github.com/Nicxe).

---

## Features

### 📡 Live Timing
Real-time data sourced from Pulselive (recommended) or the official motogp.com timing feed:

| Sensor | Description |
|--------|-------------|
| Session Status | Current session state (In Progress, Finished, Red Flag, …) |
| Current Session | Session type abbreviation (RAC, Q1, FP1, SPR, …) |
| Race Lap Count | Current lap number across all riders |
| Rider List | All riders in the current session with team and bike info |
| Rider Positions | Full position board with gaps, intervals, and last lap time |
| Top Three | Podium snapshot — leader's name as state |
| Leader | Current session leader with full detail attributes |
| Fastest Lap | Approximate fastest lap in the session |
| Session Time Remaining | Remaining session time (when available in the payload) |
| Track Weather | Air/track temperature, humidity, wind from live head data |
| Pit Stops | Count of riders currently in pit with a full rider list |

### 📅 Season Data
Polled once daily from the Pulselive REST API:

| Sensor / Entity | Description |
|-----------------|-------------|
| Next Race | Name, circuit, country, and dates of the next event |
| Current Season | Active season year with totals |
| Rider Standings | Full championship standings list |
| Constructor Standings | Team/constructor championship standings |
| Last Race Results | Classification of the most recently completed race |
| Season Calendar | HA Calendar entity with all GP events |

### 🔴 Binary Sensors
| Sensor | Description |
|--------|-------------|
| Race Week | ON during a MotoGP race weekend (configurable window start day + 3 h grace period) |
| Live Timing Online | ON when at least one live timing source is reachable |

### 🎛️ Controls & Diagnostics
| Entity | Description |
|--------|-------------|
| No Spoiler Mode | Switch — hides live/race result data when travelling or watching on delay |
| Live Source | Select — choose between Pulselive, Official, or Auto |
| Live Timing Source | Diagnostic sensor showing the active live feed |
| Official Live Timing Diagnostic | Health status of the official motogp.com feed |

### 📆 Device Automations
Nine built-in device triggers for use in HA automations:
`race_week_started`, `race_week_ended`, `session_in_progress`, `session_finished`, `session_red_flag`, `session_cancelled`, `session_delayed`, `live_timing_online`, `live_timing_offline`

---

## Installation

### HACS (recommended)

1. Open HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/RickyLeRoi/motogp_sensor` with category **Integration**
3. Search for **MotoGP Sensor** and install it
4. Restart Home Assistant

### Manual

1. Copy the `custom_components/motogp_sensor` folder into your HA `custom_components` directory
2. Restart Home Assistant

---

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **MotoGP Sensor** and follow the configuration steps:
   - Choose a device name
   - Select which sensors to enable
   - Pick a live timing source (Pulselive recommended)
   - Set the race week window start day (Monday by default)

---

## Configuration options

| Option | Description | Default |
|--------|-------------|---------|
| Device name | Label for the HA device | `MotoGP` |
| Sensors to enable | Multi-select of all available sensor keys | All enabled |
| Live timing source | `pulselive` / `official` / `auto` | `pulselive` |
| Race week start day | Day from which the Race Week sensor turns ON | `monday` |

To change options after setup, go to **Settings → Devices & Services → MotoGP Sensor → Configure**.

---

## Data sources

| Source | Endpoint | Notes |
|--------|----------|-------|
| Pulselive (default) | `api.motogp.pulselive.com/motogp/v1` | REST + live-timing-lite polling |
| Official (experimental) | `www.motogp.com/en/json/live_timing` | Same backend, different URL |

Live timing is polled every **10 seconds** when a session is active, and every **5 minutes** otherwise.

---

> [!NOTE]
> MotoGP Sensor is an unofficial project and is not affiliated with or endorsed by Dorna Sports or MotoGP. MotoGP, the MotoGP logo, and related marks are trademarks of Dorna Sports S.L.
