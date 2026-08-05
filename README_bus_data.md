# Hyderabad Transit — full bus dataset

The web app (`hyderabad-transit.html`) ships with the Metro (Red/Blue/Green) and
MMTS networks plus a small built-in bus sample. To load **all ~700 TSRTC city-bus
routes** (with full stop sequences + timings) you scrape them once, then import.

## 1. Scrape every route

```bash
pip install requests beautifulsoup4
python scrape_hyderabad_buses.py            # ~780 page loads, cached to ./cache
```

- Crawls listing pages 1–78, then each `/route-no/<slug>/` page.
- Extracts both directions, every stop (name + slug), and first-bus timings.
- Writes `bus_routes.json`.
- Resumable: pages are cached, so re-running continues where it left off.
- Be patient and polite — there's a 1s delay between live requests (~15–20 min).

Quick test first:

```bash
python scrape_hyderabad_buses.py --limit 20      # only 20 routes
```

Optional map coordinates (so bus stops/lines draw on the map):

```bash
python scrape_hyderabad_buses.py --geocode       # +~35 min via OpenStreetMap Nominatim
```

> Without `--geocode`, routes still compute correctly — bus stops just aren't
> plotted until they have lat/lon. Routing uses the stop graph, not coordinates.

## 2. Open the app — it loads automatically

The scraper also writes `bus_routes.data.js` (a script-loadable twin of
`bus_routes.json`). `hyderabad-transit.html` loads it via a `<script src>` tag
and imports it on page open — no manual import step, and it works opened
directly as a `file://` page (unlike `fetch()`, which Chrome blocks for local
files).

Just open `hyderabad-transit.html` in a browser. The full network loads instantly:

- Routes that share a stop transfer to each other automatically, including
  near-duplicate stop names/slugs for the same physical stop (handled in
  `bridgeBusStops()`).
- `BUS_BRIDGE` (top of the script) links bus stops to Metro/MMTS stations for
  cross-modal trips. Add entries there to connect more interchanges.
- Re-run the scraper any time to refresh `bus_routes.data.js`, then just
  refresh the page.

## 3. Adding real-time data later

The schema keeps per-stop timings (`"t"`) and trips/day, so you can layer live
data on top: replace the static `hop`/`headway` estimates with feed values, or
key arrivals by `stop slug`. The graph + router need no changes — just richer
edge weights.

## Files
- `scrape_hyderabad_buses.py` — the crawler/exporter; writes `bus_routes.json` and `bus_routes.data.js`
- `bus_routes.data.js` — script-loadable dataset, auto-loaded by the app on open
- `bus_routes.sample.json` — real output for 2 routes (8A, 5K/92A), for reference
- `hyderabad-transit.html` — the app

Data: routes © TSRTC, via hyderabadcitybus.in. Metro/MMTS per HMRL & Telangana Open Data.
