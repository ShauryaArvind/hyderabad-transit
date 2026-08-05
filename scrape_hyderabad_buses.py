#!/usr/bin/env python3
"""
scrape_hyderabad_buses.py
=========================
Crawl https://hyderabadcitybus.in and export EVERY TSRTC city-bus route
(with full ordered stop sequences + per-stop first-bus timings) into a single
JSON file in the schema used by the Hyderabad Transit web app.

WHY THIS RUNS ON YOUR MACHINE
-----------------------------
The full dataset is ~78 listing pages + ~700 route pages (~780 requests). This
script caches every page to ./cache so it is resumable and polite to the site.

USAGE
-----
    pip install requests beautifulsoup4
    python scrape_hyderabad_buses.py                  # scrape everything
    python scrape_hyderabad_buses.py --limit 20       # quick test: first 20 routes
    python scrape_hyderabad_buses.py --geocode        # also fetch lat/lon (slow)
    python scrape_hyderabad_buses.py --out routes.json

OUTPUT (bus_routes.json)
------------------------
{
  "source": "...", "operator": "TSRTC", "generated": "ISO-8601",
  "stops": { "<slug>": {"name":..., "routes":[codes], "lat":null, "lon":null}, ... },
  "routes": [
    { "code":"8A", "slug":"8a", "from":..., "to":..., "trips":16, "num_stops":37,
      "directions":[ {"headsign":..., "stops":[{"seq":1,"name":...,"slug":...,"t":"5:50 AM"}]} ] }
  ]
}

Then in the web app click "Import bus data" and select this file.
"""
import argparse, json, os, re, time, hashlib, sys
from datetime import datetime, timezone

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing deps. Run:  pip install requests beautifulsoup4")

BASE   = "https://hyderabadcitybus.in"
CACHE  = "cache"
DELAY  = 1.0           # seconds between live requests (be polite)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HydTransitScraper/1.0; personal project)"}
LISTING_PAGES = 78


# --------------------------------------------------------------------------- #
#  Fetching (with on-disk cache + retries)
# --------------------------------------------------------------------------- #
def fetch(url, session, retries=3):
    os.makedirs(CACHE, exist_ok=True)
    key = hashlib.md5(url.encode()).hexdigest() + ".html"
    path = os.path.join(CACHE, key)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    for attempt in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                time.sleep(DELAY)
                return r.text
            if r.status_code == 404:
                return None
        except requests.RequestException as e:
            print(f"  ! {url} attempt {attempt+1}: {e}")
        time.sleep(2 * (attempt + 1))
    print(f"  !! giving up on {url}")
    return None


def slugify(name):
    s = name.lower().strip()
    s = re.sub(r"[./]", "-", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


# --------------------------------------------------------------------------- #
#  Step 1 — collect every /route-no/<slug>/ URL from the 78 listing pages
# --------------------------------------------------------------------------- #
def collect_route_urls(session):
    urls = {}
    for p in range(1, LISTING_PAGES + 1):
        url = BASE + "/" if p == 1 else f"{BASE}/page/{p}/"
        print(f"[listing {p}/{LISTING_PAGES}] {url}")
        html = fetch(url, session)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select('a[href*="/route-no/"]'):
            href = a["href"].split("#")[0].rstrip("/") + "/"
            slug = href.rstrip("/").split("/")[-1]
            urls[slug] = href
    print(f"  -> found {len(urls)} unique route pages")
    return urls


# --------------------------------------------------------------------------- #
#  Step 2 — parse a single route page
# --------------------------------------------------------------------------- #
INTRO_RE = re.compile(
    r"runs between\s+(.+?)\s+and\s+(.+?)\.\s*This.*?about\s+([\d,]+)\s+trips.*?total of\s+([\d,]+)\s+bus stops",
    re.S | re.I)


def parse_route(url, html):
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    code = h1.get_text(strip=True) if h1 else url.rstrip("/").split("/")[-1]
    slug = url.rstrip("/").split("/")[-1]

    text = soup.get_text(" ", strip=True)
    m = INTRO_RE.search(text)
    frm = to = ""; trips = num_stops = None
    if m:
        frm, to = m.group(1).strip(), m.group(2).strip()
        trips = int(m.group(3).replace(",", ""))
        num_stops = int(m.group(4).replace(",", ""))

    # name -> slug map from the bus-stop link list
    name2slug = {}
    for a in soup.select('a[href*="/bus-stop/"]'):
        nm = a.get_text(strip=True)
        sl = a["href"].rstrip("/").split("/")[-1]
        if nm and "," not in sl:        # skip the multi-slug nav links
            name2slug[nm.lower()] = sl

    # every stop table has headers: Stop No. | Bus Stop Name | First Bus Timings
    directions = []
    for table in soup.find_all("table"):
        head = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not any("bus stop name" in h for h in head):
            continue
        stops = []
        for tr in table.find_all("tr"):
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(tds) < 2 or not tds[0].isdigit():
                continue
            nm = tds[1]
            t = tds[2] if len(tds) > 2 else None
            stops.append({"seq": int(tds[0]), "name": nm,
                          "slug": name2slug.get(nm.lower(), slugify(nm)), "t": t})
        if stops:
            directions.append({"headsign": stops[-1]["name"], "stops": stops})

    return {"code": code, "slug": slug, "from": frm, "to": to,
            "trips": trips, "num_stops": num_stops, "directions": directions}


# --------------------------------------------------------------------------- #
#  Optional geocoding via Nominatim (OpenStreetMap)
# --------------------------------------------------------------------------- #
def geocode_stops(stops, session):
    cache_path = "geocode_cache.json"
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    n = len(stops)
    for i, (slug, s) in enumerate(stops.items(), 1):
        if slug in cache:
            s["lat"], s["lon"] = cache[slug]; continue
        q = f"{s['name']}, Hyderabad, Telangana, India"
        try:
            r = session.get("https://nominatim.openstreetmap.org/search",
                            params={"q": q, "format": "json", "limit": 1},
                            headers=HEADERS, timeout=30)
            arr = r.json()
            if arr:
                s["lat"], s["lon"] = float(arr[0]["lat"]), float(arr[0]["lon"])
                cache[slug] = [s["lat"], s["lon"]]
        except Exception as e:
            print(f"  geocode fail {slug}: {e}")
        if i % 25 == 0:
            json.dump(cache, open(cache_path, "w"))
            print(f"  geocoded {i}/{n}")
        time.sleep(1.1)                      # Nominatim policy: <=1 req/sec
    json.dump(cache, open(cache_path, "w"))


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only first N routes (testing)")
    ap.add_argument("--geocode", action="store_true", help="fetch lat/lon (slow)")
    ap.add_argument("--out", default="bus_routes.json")
    args = ap.parse_args()

    session = requests.Session()
    route_urls = collect_route_urls(session)
    slugs = list(route_urls)
    if args.limit:
        slugs = slugs[:args.limit]

    routes, stops = [], {}
    for i, slug in enumerate(slugs, 1):
        url = route_urls[slug]
        print(f"[route {i}/{len(slugs)}] {slug}")
        html = fetch(url, session)
        if not html:
            continue
        rt = parse_route(url, html)
        routes.append(rt)
        for d in rt["directions"]:
            for st in d["stops"]:
                rec = stops.setdefault(st["slug"], {"name": st["name"], "routes": [],
                                                    "lat": None, "lon": None})
                if rt["code"] not in rec["routes"]:
                    rec["routes"].append(rt["code"])

    if args.geocode:
        print(f"Geocoding {len(stops)} stops via Nominatim (~{len(stops)*1.1/60:.0f} min)...")
        geocode_stops(stops, session)

    out = {"source": BASE, "operator": "TSRTC",
           "generated": datetime.now(timezone.utc).isoformat(),
           "stops": stops, "routes": routes}
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # Also write a script-loadable twin so hyderabad-transit.html can load the
    # full dataset on open via <script src>, with no manual import step (and
    # no file:// CORS issues, unlike fetch()/XHR of a local .json file). The
    # app only ever reads stop.name/lat/lon and route.code/slug/from/to/trips/
    # directions[].stops[].slug/.t — everything else here (per-stop "routes"
    # list, per-stop-visit "name"/"seq", "headsign", "num_stops") exists for
    # bus_routes.json's human/debugging value but would otherwise double the
    # payload every browser has to download and parse on load for no benefit.
    slim = {"source": out["source"], "operator": out["operator"], "generated": out["generated"],
            "stops": {slug: {"name": s["name"], "lat": s["lat"], "lon": s["lon"]}
                      for slug, s in stops.items()},
            "routes": [{"code": rt["code"], "slug": rt["slug"], "from": rt["from"], "to": rt["to"],
                        "trips": rt["trips"],
                        "directions": [{"stops": [{"slug": st["slug"], "t": st["t"]} for st in d["stops"]]}
                                       for d in rt["directions"]]}
                       for rt in routes]}
    data_js = os.path.splitext(args.out)[0] + ".data.js"
    with open(data_js, "w", encoding="utf-8") as f:
        f.write("window.BUS_DATA = ")
        json.dump(slim, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";")

    print(f"\nDone. {len(routes)} routes, {len(stops)} unique stops -> {args.out} & {data_js}")


if __name__ == "__main__":
    main()
