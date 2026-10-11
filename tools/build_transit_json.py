#!/usr/bin/env python3
"""產生 App 用的 transit.json。

兩種用法：

1) 只用現有網站資料（61 站、8 條路線）：
   python3 tools/build_transit_json.py --legacy ../../Auckland2026_Transit/source/auckland-map.json

2) 加上 AT 官方 GTFS（全部巴士站、火車站、渡輪站與渡輪航線）：
   先到 https://at.govt.nz/about-us/at-data-sources/general-transit-feed-specification/
   下載 gtfs.zip，然後：
   python3 tools/build_transit_json.py --legacy .../auckland-map.json --gtfs ~/Downloads/gtfs.zip

輸出預設寫到 AucklandTransit/Resources/transit.json。
網站的中文站名與 8 條精選路線（含官方色）會保留，GTFS 只負責補齊所有站點與渡輪。
"""
import argparse
import csv
import datetime as _dt
import io
import json
import math
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.normpath(os.path.join(HERE, "..", "Resources", "transit.json"))

MODE_ORDER = ["train", "bus", "ferry"]

# 網站資料沒有的火車站與渡輪碼頭中文名（台灣慣用譯名；只套用在火車站與渡輪碼頭）
EXTRA_ZH = {
    "The Strand": "斯特蘭德",
    "Huntly": "亨特利",
    "Hamilton Frankton": "漢彌爾頓弗蘭克頓",
    "Hamilton Rotokauri": "漢彌爾頓羅托考里",
    "Downtown Ferry Terminal": "市中心渡輪碼頭",
    "Devonport Ferry Terminal": "德文港渡輪碼頭",
    "Waiheke Ferry Terminal": "激流島渡輪碼頭",
    "Kennedy Point Ferry Terminal": "甘迺迪角渡輪碼頭（激流島）",
    "Bayswater Ferry Terminal": "貝斯沃特渡輪碼頭",
    "Birkenhead Ferry Terminal": "伯肯黑德渡輪碼頭",
    "Beach Haven Ferry Terminal": "比奇黑文渡輪碼頭",
    "Gulf Harbour Ferry Terminal": "海灣港渡輪碼頭",
    "Half Moon Bay Ferry Terminal": "半月灣渡輪碼頭",
    "Half Moon Bay Ferry Terminal - SeaLink": "半月灣渡輪碼頭（SeaLink 汽車渡輪）",
    "Hamer Street Ferry Terminal": "哈默街渡輪碼頭（SeaLink 汽車渡輪）",
    "Hobsonville Point Ferry Terminal": "霍布森維爾角渡輪碼頭",
    "Pine Harbour Ferry Terminal": "松樹港渡輪碼頭",
    "Rakino Ferry Terminal": "拉基諾島渡輪碼頭",
    "Rangitoto Wharf Ferry Terminal": "朗伊托托島碼頭",
    "Te Onewa Northcote Point Ferry Terminal": "諾斯科特角渡輪碼頭",
    "Tiritiri Matangi Island Wharf": "蒂里蒂里馬唐伊島碼頭",
    "West Harbour Ferry Terminal": "西港渡輪碼頭",
}
FERRY_DEFAULT_COLOR = "0A7CC1"
# 這份資料的版本號（產生當下的 UTC 秒數）。App 用它判斷網路上的資料是不是比手上的新。
BUILD = int(os.environ.get("DATA_BUILD") or _dt.datetime.now(_dt.timezone.utc).timestamp())


def gtfs_mode(route_type):
    """GTFS route_type（含延伸代碼）→ train / bus / ferry；其他（纜車等）回傳 None。"""
    try:
        t = int(route_type)
    except (TypeError, ValueError):
        return None
    if t in (1, 2, 12) or 100 <= t < 200 or 400 <= t < 500:
        return "train"
    if t in (3, 11) or 200 <= t < 300 or 700 <= t < 800 or 800 <= t < 900:
        return "bus"
    if t == 4 or 1000 <= t < 1300:
        return "ferry"
    return None


def haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def natural_key(s):
    out, num = [], ""
    for ch in s:
        if ch.isdigit():
            num += ch
        else:
            if num:
                out.append((0, int(num), ""))
                num = ""
            out.append((1, 0, ch))
    if num:
        out.append((0, int(num), ""))
    return out


def sort_routes(keys):
    return sorted(set(keys), key=natural_key)


def mode_list(modes):
    return [m for m in MODE_ORDER if m in modes]


def thin(points, max_points=160):
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    return [points[round(i * step)] for i in range(max_points)]


def clean_name(name):
    """AT 的上層車站名稱有些是「Ferry Terminal - Devonport」，改成站牌上常見的「Devonport Ferry Terminal」。"""
    prefix = "Ferry Terminal - "
    if name.startswith(prefix):
        return name[len(prefix):].strip() + " Ferry Terminal"
    if name.endswith(" Train Station"):
        return name[: -len(" Train Station")].strip()
    return name


def usable_color(hex_color):
    c = (hex_color or "").strip().lstrip("#").upper()
    if len(c) != 6 or c in ("000000", "FFFFFF"):
        return None
    return c


def pretty_headsign(headsign):
    """「Downtown Pier 4 To Devonport Via ...」→「Downtown Pier 4 ↔ Devonport」。"""
    h = re.sub(r"\s+Via\s+.*$", "", headsign.strip(), flags=re.I)
    h = re.sub(r"\s+(Anti[- ]?)?Clockwise", "", h, flags=re.I)
    parts = [re.sub(r"(?<!Pier)\s+\d+\b", "", p.strip()).strip() for p in re.split(r"\s+To\s+", h, flags=re.I)]
    parts = [p for p in parts if p]
    if len(parts) == 2 and parts[1].lower().startswith("downtown"):
        parts.reverse()
    return " ↔ ".join(parts)


def route_display_name(counter):
    """依班次最多的方向決定路線名稱；有碼頭號碼（Pier）的寫法優先。"""
    if not counter:
        return ""
    ranked = [h for h, _ in counter.most_common() if h]
    if not ranked:
        return ""
    top = counter[ranked[0]]
    with_pier = [h for h in ranked if "pier" in h.lower() and counter[h] >= top * 0.5]
    return pretty_headsign(with_pier[0] if with_pier else ranked[0])


def read_csv(zf, name):
    try:
        raw = zf.open(name)
    except KeyError:
        return
    with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            yield row


# ---------------------------------------------------------------- 共用路段並排

SHARE_METERS = 30  # 兩條路線距離在這以內，就當作走同一段路


def _proj(lat0):
    mx = 111320 * math.cos(math.radians(lat0))
    my = 110540
    return lambda p: (p[1] * mx, p[0] * my)


def _unit(dx, dy):
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-6 else (0.0, 0.0)


def _closest_on_polyline(pt, poly):
    """回傳 (距離, 該線段方向單位向量)。"""
    best = (float("inf"), (0.0, 0.0))
    px, py = pt
    for (ax, ay), (bx, by) in zip(poly, poly[1:]):
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        tt = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
        cx, cy = ax + tt * dx, ay + tt * dy
        d = math.hypot(px - cx, py - cy)
        if d < best[0]:
            best = (d, _unit(dx, dy))
    return best


def assign_lanes(routes):
    """在每個點加上第三個值：並排時的車道偏移（單位＝一條線寬，以路線自己的左手邊為正）。

    同一段路有 k 條同類型路線時，依路線順序排成 k 條並排線，置中對齊。
    方向以該路段第一條路線為準，反方向行駛的路線會自動翻轉，避免兩條線偏到同一邊。
    App 依照縮放程度把偏移換算成公尺。
    """
    if not routes:
        return
    lat0 = routes[0]["paths"][0][0][0]
    proj = _proj(lat0)
    order = {r["key"]: i for i, r in enumerate(routes)}
    xy = {r["key"]: [[proj(p) for p in path] for path in r["paths"]] for r in routes}
    for r in routes:
        others = [o for o in routes if o["key"] != r["key"] and o["mode"] == r["mode"]]
        for pi, path in enumerate(r["paths"]):
            pts = xy[r["key"]][pi]
            for vi, point in enumerate(path):
                a, b = pts[max(0, vi - 1)], pts[min(len(pts) - 1, vi + 1)]
                own = _unit(b[0] - a[0], b[1] - a[1])
                group = {r["key"]: own}
                for o in others:
                    best = (float("inf"), (0.0, 0.0))
                    for opts in xy[o["key"]]:
                        cand = _closest_on_polyline(pts[vi], opts)
                        if cand[0] < best[0]:
                            best = cand
                    if best[0] <= SHARE_METERS:
                        group[o["key"]] = best[1]
                lane = 0.0
                if len(group) > 1:
                    keys = sorted(group, key=lambda k: order[k])
                    anchor = group[keys[0]]
                    sign = 1.0 if own[0] * anchor[0] + own[1] * anchor[1] >= 0 else -1.0
                    lane = (keys.index(r["key"]) - (len(keys) - 1) / 2) * sign
                del point[2:]
                if lane:
                    point.append(round(lane, 2))


# ---------------------------------------------------------------- legacy

def load_legacy(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    route_mode = {r["key"]: r["mode"] for r in data["routes"]}
    routes = [{
        "key": r["key"], "name": r["name"], "color": r["color"].lstrip("#").upper(),
        "mode": r["mode"], "dash": bool(r.get("dash")),
        "paths": [[[round(p[0], 6), round(p[1], 6)] for p in path] for path in r["paths"]],
    } for r in data["routes"]]
    stops = []
    for i, s in enumerate(data["stations"]):
        modes = {route_mode[k] for k in s.get("routes", []) if k in route_mode}
        if s.get("train"):
            modes.add("train")
        if not modes:
            modes.add("bus")
        stops.append({
            "id": "legacy-%02d" % i,
            "name": s["name"],
            "zh": s.get("zh") or None,
            "code": None,
            "lat": round(s["lat"], 6),
            "lon": round(s["lon"], 6),
            "modes": mode_list(modes),
            "routes": sort_routes(s.get("routes", [])),
            "major": True,
        })
    return data, routes, stops


# ---------------------------------------------------------------- GTFS

def load_gtfs(path):
    zf = zipfile.ZipFile(path)

    routes = {}
    for r in read_csv(zf, "routes.txt"):
        mode = gtfs_mode(r.get("route_type"))
        if not mode:
            continue
        short = (r.get("route_short_name") or "").strip() or (r.get("route_long_name") or "").strip()
        routes[r["route_id"]] = {
            "mode": mode, "short": short,
            "long": (r.get("route_long_name") or "").strip(),
            "color": (r.get("route_color") or "").strip().upper(),
        }

    trip_route = {}
    ferry_shapes = defaultdict(set)
    headsigns = defaultdict(Counter)
    for t in read_csv(zf, "trips.txt"):
        rid = t.get("route_id")
        if rid in routes:
            trip_route[t["trip_id"]] = rid
            headsigns[routes[rid]["short"]][(t.get("trip_headsign") or "").strip()] += 1
            if routes[rid]["mode"] == "ferry" and t.get("shape_id"):
                ferry_shapes[rid].add(t["shape_id"])

    raw_stops = {}
    for s in read_csv(zf, "stops.txt"):
        try:
            lat, lon = float(s["stop_lat"]), float(s["stop_lon"])
        except (KeyError, ValueError):
            continue
        name = (s.get("stop_name") or "").strip()
        if name.startswith("Virtual Point"):
            continue  # 票價計算用的虛擬點，不是真的站
        raw_stops[s["stop_id"]] = {
            "name": clean_name(name),
            "code": (s.get("stop_code") or "").strip() or None,
            "lat": lat, "lon": lon,
            "loc_type": (s.get("location_type") or "0").strip() or "0",
            "parent": (s.get("parent_station") or "").strip() or None,
        }

    stop_routes = defaultdict(set)
    stop_modes = defaultdict(set)
    for st in read_csv(zf, "stop_times.txt"):
        rid = trip_route.get(st.get("trip_id"))
        if not rid:
            continue
        sid = st["stop_id"]
        r = routes[rid]
        stop_routes[sid].add(r["short"])
        stop_modes[sid].add(r["mode"])

    # 有上層車站（parent_station）的月台／站位合併成一個點：外國人只需要知道「去哪個車站」
    merged = {}
    for sid in stop_modes:
        s = raw_stops.get(sid)
        if not s:
            continue
        key = s["parent"] if s["parent"] and s["parent"] in raw_stops else sid
        base = raw_stops[key]
        m = merged.setdefault(key, {
            "id": key, "name": base["name"],
            "code": None if key != sid else base["code"],
            "lat": base["lat"], "lon": base["lon"],
            "modes": set(), "routes": set(), "station": key != sid,
        })
        m["modes"] |= stop_modes[sid]
        m["routes"] |= stop_routes[sid]

    # 每條路線的名稱（起訖點）與顏色，點車站時顯示
    route_info = {}
    for r in routes.values():
        key = r["short"]
        if key in route_info:
            continue
        name = route_display_name(headsigns.get(key))
        if not name or name == key:
            name = r["long"] if r["long"] and r["long"] != key else ""
        ends = name.split(" ↔ ") if " ↔ " in name else None
        route_info[key] = {"name": name, "mode": r["mode"], "color": usable_color(r["color"]),
                           "ends": ends if ends and len(ends) == 2 else None}

    # 渡輪航線形狀（每條航線取點數最多的 shape）
    ferry_routes = []
    if ferry_shapes:
        wanted = set().union(*ferry_shapes.values())
        pts = defaultdict(list)
        for row in read_csv(zf, "shapes.txt"):
            if row.get("shape_id") in wanted:
                pts[row["shape_id"]].append((int(row.get("shape_pt_sequence") or 0),
                                             float(row["shape_pt_lat"]), float(row["shape_pt_lon"])))
        for rid, shapes in ferry_shapes.items():
            best = max(shapes, key=lambda sh: len(pts.get(sh, [])))
            seq = sorted(pts.get(best, []))
            if len(seq) < 2:
                continue
            r = routes[rid]
            ferry_routes.append({
                "key": r["short"], "name": (r["short"] + " " + route_info[r["short"]]["name"]).strip(),
                "color": usable_color(r["color"]) or FERRY_DEFAULT_COLOR, "mode": "ferry", "dash": True,
                "paths": [thin([[round(a, 6), round(b, 6)] for _, a, b in seq])],
            })
        # 同一個 key 只留一條
        seen, uniq = set(), []
        for r in sorted(ferry_routes, key=lambda r: natural_key(r["key"])):
            if r["key"] not in seen:
                seen.add(r["key"])
                uniq.append(r)
        ferry_routes = uniq

    return list(merged.values()), ferry_routes, route_info


def merge(legacy_stops, gtfs_stops):
    """把網站的中文站名、精選路線代號掛到 GTFS 站點上；找不到對應的保留原網站站點。"""
    out = []
    for g in gtfs_stops:
        out.append({
            "id": g["id"], "name": g["name"], "zh": None, "code": g["code"],
            "lat": round(g["lat"], 6), "lon": round(g["lon"], 6),
            "modes": set(g["modes"]), "routes": set(g["routes"]),
            "major": g["station"] or "train" in g["modes"] or "ferry" in g["modes"],
            "_station": g["station"],
        })
    unmatched = 0
    for L in legacy_stops:
        best, best_d = None, None
        # 網站上標為火車站的，只對到 GTFS 火車站（避免掛到旁邊的巴士轉運站）
        wanted = {"train"} if "train" in L["modes"] else set(L["modes"])
        for g in out:
            if not (wanted & g["modes"]):
                continue
            d = haversine(L["lat"], L["lon"], g["lat"], g["lon"])
            limit = 350 if g["_station"] or "train" in g["modes"] else 120
            if d <= limit and (best_d is None or d < best_d):
                best, best_d = g, d
        if best is None:
            unmatched += 1
            item = dict(L)
            item["modes"] = set(L["modes"])
            item["routes"] = set(L["routes"])
            item["_station"] = True
            out.append(item)
            continue
        if not best["zh"]:
            best["zh"] = L["zh"]
            best["name"] = L["name"]  # 網站用的是簡潔站名（不含 Train Station 字尾）
        # 只補路線代號（轉乘資訊）；交通工具種類以 GTFS 為準，
        # 避免火車站被當成「最近的巴士站」，真正的巴士站牌 GTFS 裡都有。
        best["routes"] |= set(L["routes"])
        best["major"] = True
    for s in out:
        if not s["zh"] and s["name"] in EXTRA_ZH and ({"train", "ferry"} & s["modes"]):
            s["zh"] = EXTRA_ZH[s["name"]]
        s.pop("_station", None)
        s["modes"] = mode_list(s["modes"])
        s["routes"] = sort_routes(s["routes"])
    out.sort(key=lambda s: (not s["major"], s["name"]))
    return out, unmatched


# ---------------------------------------------------------------- 時刻表

def destination(headsign):
    """「Manukau 1 To Swanson 1 Via Waitemata 4」→「Swanson」：班次開往的終點。"""
    h = re.sub(r"\s+Via\s+.*$", "", (headsign or "").strip(), flags=re.I)
    h = re.sub(r"\s+(Anti[- ]?)?Clockwise", "", h, flags=re.I)
    last = re.split(r"\s+To\s+", h, flags=re.I)[-1]
    return re.sub(r"(?<!Pier)\s+\d+\b", "", last).strip()


def _minutes(hms):
    try:
        h, m = hms.split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def build_timetable(gtfs_path, out_path):
    """產生 timetable.sqlite：每個站（與 transit.json 相同的站 id）每條路線、每個方向的表定發車時間。

    - 時間是「服務日 0 點起算的分鐘數」，可能超過 1440（GTFS 的跨午夜班次）。
    - 同一路線、方向、服務規則的時間合併成一列，用差值編碼的逗號字串存，App 查單站時才解開。
    - 不收班次的最後一站（只下車）與不能上車的站（pickup_type=1）。
    - 這些「只下車」的站另外存在 arrivals 表（班次、站、到站分鐘），App 規劃搭車路線時才知道終點站幾點到。
    """
    import sqlite3
    zf = zipfile.ZipFile(gtfs_path)

    routes = {}
    for r in read_csv(zf, "routes.txt"):
        mode = gtfs_mode(r.get("route_type"))
        if mode:
            short = (r.get("route_short_name") or "").strip() or (r.get("route_long_name") or "").strip()
            routes[r["route_id"]] = (short, mode)

    # 站 id 合併規則與 load_gtfs 相同
    kept, parent = set(), {}
    for s in read_csv(zf, "stops.txt"):
        if (s.get("stop_name") or "").strip().startswith("Virtual Point"):
            continue
        kept.add(s["stop_id"])
        parent[s["stop_id"]] = (s.get("parent_station") or "").strip() or None
    def stop_key(sid):
        p = parent.get(sid)
        return p if p and p in kept else sid

    services, service_ids = [], {}
    def service_index(sid):
        if sid not in service_ids:
            service_ids[sid] = len(services)
            services.append([sid, "0000000", 0, 0])
        return service_ids[sid]
    for c in read_csv(zf, "calendar.txt"):
        i = service_index(c["service_id"])
        days = "".join(c[d] for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))
        services[i][1:] = [days, int(c["start_date"]), int(c["end_date"])]
    service_dates = [(service_index(d["service_id"]), int(d["date"]), int(d["exception_type"]))
                     for d in read_csv(zf, "calendar_dates.txt")]

    trips = {}
    for tr in read_csv(zf, "trips.txt"):
        if tr.get("route_id") in routes:
            trips[tr["trip_id"]] = (routes[tr["route_id"]], service_index(tr["service_id"]),
                                    (tr.get("trip_headsign") or "").strip())

    patterns, pattern_ids = [], {}
    groups = defaultdict(list)  # (stop, pattern, service) -> [(minutes, trip index)]
    trip_list, trip_index = [], {}
    arrivals = []   # (trip index, stop, minutes)：只下車的站（終點站、pickup_type=1）

    def flush(trip_id, rows):
        info = trips.get(trip_id)
        if not info or len(rows) < 2:
            return
        (short, mode), svc, headsign = info
        rows.sort(key=lambda r: int(r["stop_sequence"] or 0))
        if trip_id not in trip_index:
            trip_index[trip_id] = len(trip_list)
            trip_list.append(trip_id)
        ti = trip_index[trip_id]
        for idx, r in enumerate(rows):
            last = idx == len(rows) - 1          # 最後一站只下車
            if last or (r.get("pickup_type") or "0") == "1":
                if (r.get("drop_off_type") or "0") != "1":
                    m = _minutes(r.get("arrival_time") or r.get("departure_time"))
                    if m is not None:
                        arrivals.append((ti, stop_key(r["stop_id"]), m))
                continue
            m = _minutes(r.get("departure_time") or r.get("arrival_time"))
            if m is None:
                continue
            dest = destination(headsign) or (r.get("stop_headsign") or "").strip().title()
            pk = (short, mode, dest)
            if pk not in pattern_ids:
                pattern_ids[pk] = len(patterns)
                patterns.append(pk)
            groups[(stop_key(r["stop_id"]), pattern_ids[pk], svc)].append((m, ti))

    current, buf = None, []
    for r in read_csv(zf, "stop_times.txt"):
        if r["trip_id"] != current:
            if current is not None:
                flush(current, buf)
            current, buf = r["trip_id"], []
        buf.append(r)
    if current is not None:
        flush(current, buf)

    if os.path.exists(out_path):
        os.remove(out_path)
    db = sqlite3.connect(out_path)
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE services(id INTEGER PRIMARY KEY, days TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL);
        CREATE TABLE service_dates(service INTEGER NOT NULL, date INTEGER NOT NULL, type INTEGER NOT NULL);
        CREATE TABLE patterns(id INTEGER PRIMARY KEY, route TEXT NOT NULL, mode TEXT NOT NULL, dest TEXT NOT NULL);
        CREATE TABLE departures(stop TEXT NOT NULL, pattern INTEGER NOT NULL, service INTEGER NOT NULL,
                                times TEXT NOT NULL, trips TEXT NOT NULL);
        CREATE TABLE trips(id INTEGER PRIMARY KEY, trip_id TEXT NOT NULL);
        CREATE TABLE arrivals(trip INTEGER NOT NULL, stop TEXT NOT NULL, minute INTEGER NOT NULL);
    """)
    info = next(read_csv(zf, "feed_info.txt") or iter(()), None) or {}
    db.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("feed_start", info.get("feed_start_date", "")), ("feed_end", info.get("feed_end_date", "")),
        ("feed_version", info.get("feed_version", "")), ("build", str(BUILD)),
        ("generated", _dt.date.today().isoformat()), ("timezone", "Pacific/Auckland")])
    db.executemany("INSERT INTO services VALUES (?, ?, ?, ?)",
                   [(i, s[1], s[2], s[3]) for i, s in enumerate(services)])
    db.executemany("INSERT INTO service_dates VALUES (?, ?, ?)", service_dates)
    db.executemany("INSERT INTO patterns VALUES (?, ?, ?, ?)",
                   [(i, p[0], p[1], p[2]) for i, p in enumerate(patterns)])
    db.executemany("INSERT INTO trips VALUES (?, ?)", list(enumerate(trip_list)))
    db.executemany("INSERT INTO arrivals VALUES (?, ?, ?)", sorted(arrivals))
    rows = []
    for (stop, pat, svc), items in groups.items():
        items.sort()
        ms = [m for m, _ in items]
        ts = [ti for _, ti in items]
        deltas = [ms[0]] + [b - a for a, b in zip(ms, ms[1:])]
        tdeltas = [ts[0]] + [b - a for a, b in zip(ts, ts[1:])]   # 班次編號也用差值存
        rows.append((stop, pat, svc, ",".join(map(str, deltas)), ",".join(map(str, tdeltas))))
    rows.sort()
    db.executemany("INSERT INTO departures VALUES (?, ?, ?, ?, ?)", rows)
    db.execute("CREATE INDEX idx_departures_stop ON departures(stop)")
    db.commit()
    db.execute("VACUUM")
    db.close()
    total = sum(len(v) for v in groups.values())
    print("時刻表：%d 個站、%d 條路線方向、%d 個發車時間，%.1f MB" % (
        len({k[0] for k in groups}), len(patterns), total, os.path.getsize(out_path) / 1e6))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--legacy", required=True, help="網站用的 auckland-map.json")
    ap.add_argument("--gtfs", help="AT 官方 gtfs.zip（可省略）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--timetable-out", default=os.path.join(os.path.dirname(DEFAULT_OUT), "timetable.sqlite"),
                    help="時刻表資料庫輸出位置（有 --gtfs 才會產生）")
    args = ap.parse_args(argv)

    legacy_data, routes, legacy_stops = load_legacy(args.legacy)
    source = legacy_data.get("source", "Auckland Transport GTFS")

    if args.gtfs:
        gtfs_stops, ferry_routes, gtfs_info = load_gtfs(args.gtfs)
        stops, unmatched = merge(legacy_stops, gtfs_stops)
        routes = routes + ferry_routes
        source = "Auckland Transport GTFS ＋ asheng.fyi 精選路線"
        with zipfile.ZipFile(args.gtfs) as zf:
            info = next(read_csv(zf, "feed_info.txt") or iter(()), None)
        if info and info.get("feed_start_date") and info.get("feed_end_date"):
            fmt = lambda d: "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
            source = "Auckland Transport GTFS（有效期 %s～%s）＋ asheng.fyi 精選路線" % (
                fmt(info["feed_start_date"]), fmt(info["feed_end_date"]))
        print("GTFS 站點 %d、網站站點未對上 %d、渡輪航線 %d" % (len(gtfs_stops), unmatched, len(ferry_routes)))
    else:
        stops = legacy_stops
        gtfs_info = {}

    # 路線資訊：GTFS 的起訖點名稱＋網站精選路線的中文名稱與官方色（精選優先）
    route_info = dict(gtfs_info)
    for r in routes:
        prefix = r["key"] + " "
        name = r["name"][len(prefix):] if r["name"].startswith(prefix) else r["name"]
        info = route_info.get(r["key"], {})
        route_info[r["key"]] = {"name": name or info.get("name", ""), "mode": r["mode"], "color": r["color"],
                                "ends": info.get("ends")}

    assign_lanes(routes)
    shared = sum(1 for r in routes for path in r["paths"] for p in path if len(p) > 2)
    print("並排路段的點：%d" % shared)

    out = {
        "generated": _dt.date.today().isoformat(),
        "build": BUILD,
        "source": source,
        "routes": routes,
        "routeInfo": dict(sorted(route_info.items(), key=lambda kv: natural_key(kv[0]))),
        "stops": stops,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    if args.gtfs:
        build_timetable(args.gtfs, args.timetable_out)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    count = defaultdict(int)
    for s in stops:
        for m in s["modes"]:
            count[m] += 1
    print("寫入 %s：%d 站（火車 %d、巴士 %d、渡輪 %d），%d 條路線，%.0f KB" % (
        args.out, len(stops), count["train"], count["bus"], count["ferry"], len(routes),
        os.path.getsize(args.out) / 1024))


if __name__ == "__main__":
    sys.exit(main())
