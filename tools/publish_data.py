#!/usr/bin/env python3
"""把時刻表資料打包成 App 可以下載更新的格式（GitHub Actions 每天自動跑，也可以手動跑）。

  # AT 的 GTFS 有沒有更新？（跟線上 manifest 比 feed_version）
  python3 tools/publish_data.py check --gtfs gtfs.zip --live https://…/v1/manifest.json

  # 產生 dist/v1/：manifest.json、timetable.sqlite.deflate、transit.json.deflate
  python3 tools/publish_data.py build --gtfs gtfs.zip --legacy source/auckland-map.json --out dist

壓縮用 raw DEFLATE（App 端用 Apple 內建的 zlib 解壓，不需要第三方套件）。
manifest 裡的 sha256 是「解壓後」檔案的雜湊，App 解壓後比對，對不上就不用。
"""
import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = 1
MIN_APP_BUILD = 1


def feed_info(gtfs):
    with zipfile.ZipFile(gtfs) as zf:
        with zf.open("feed_info.txt") as fh:
            rows = list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))
    return rows[0] if rows else {}


def deflate(src, dst):
    co = __import__("zlib").compressobj(9, 8, -15)        # wbits=-15：raw DEFLATE
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            chunk = fi.read(1 << 20)
            if not chunk:
                break
            fo.write(co.compress(chunk))
        fo.write(co.flush())


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_check(args):
    info = feed_info(args.gtfs)
    version = info.get("feed_version", "")
    live = None
    try:
        with urllib.request.urlopen(args.live, timeout=30) as r:
            live = json.load(r)
    except Exception as e:                                   # 還沒有線上版本（第一次）也算「有更新」
        print("讀不到線上 manifest：%s" % e)
    changed = not live or live.get("feedVersion") != version or live.get("schema") != SCHEMA
    print("GTFS feed_version=%s，線上=%s → %s" % (version, live and live.get("feedVersion"), "需要更新" if changed else "沒有變"))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write("changed=%s\n" % ("true" if changed else "false"))
    return 0


def cmd_build(args):
    work = tempfile.mkdtemp()
    transit = os.path.join(work, "transit.json")
    timetable = os.path.join(work, "timetable.sqlite")
    subprocess.check_call([sys.executable, os.path.join(HERE, "build_transit_json.py"),
                           "--legacy", args.legacy, "--gtfs", args.gtfs,
                           "--out", transit, "--timetable-out", timetable])

    db = sqlite3.connect(timetable)
    meta = dict(db.execute("SELECT key, value FROM meta"))
    db.close()
    with open(transit, encoding="utf-8") as fh:
        tj = json.load(fh)
    assert str(tj.get("build")) == meta["build"], "transit.json 與 timetable.sqlite 的版本不一致"
    assert len(tj["stops"]) > 1000, "站點數量不合理"

    out = os.path.join(args.out, "v1")
    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    os.makedirs(out)
    files = {}
    for key, path in (("timetable", timetable), ("transit", transit)):
        name = os.path.basename(path) + ".deflate"
        deflate(path, os.path.join(out, name))
        files[key] = {"path": name, "sha256": sha256(path), "bytes": os.path.getsize(path),
                      "deflatedBytes": os.path.getsize(os.path.join(out, name))}
    manifest = {
        "schema": SCHEMA,
        "build": int(meta["build"]),
        "feedStart": int(meta["feed_start"] or 0),
        "feedEnd": int(meta["feed_end"] or 0),
        "feedVersion": meta.get("feed_version", ""),
        "generated": meta.get("generated", ""),
        "minAppBuild": MIN_APP_BUILD,
        "source": "Auckland Transport GTFS (https://at.govt.nz), CC BY 4.0; converted for Easy Ride Auckland",
        "files": files,
    }
    with open(os.path.join(out, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write("<!doctype html><meta charset=utf-8><title>Easy Ride Auckland data</title>"
                 "<p>Timetable data for the Easy Ride Auckland app, rebuilt automatically from "
                 "Auckland Transport's public GTFS feed.</p>"
                 "<p>Contains data from <a href='https://at.govt.nz/about-us/at-data-sources/'>Auckland Transport</a>, "
                 "licensed under <a href='https://creativecommons.org/licenses/by/4.0/'>CC BY 4.0</a>. "
                 "The data has been converted and compressed for the app.</p>"
                 "<p>Feed valid %s – %s. <a href='v1/manifest.json'>manifest</a></p>" % (
                     manifest["feedStart"], manifest["feedEnd"]))
    # 網站頁面（首頁、隱私權政策、支援頁）一起發布；site/index.html 會取代上面的簡易首頁
    site = os.path.join(os.path.dirname(HERE), "site")
    if os.path.isdir(site):
        shutil.copytree(site, args.out, dirs_exist_ok=True)
    shutil.rmtree(work)
    print(json.dumps(manifest, indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--gtfs", required=True)
    c.add_argument("--live", required=True)
    b = sub.add_parser("build")
    b.add_argument("--gtfs", required=True)
    b.add_argument("--legacy", required=True)
    b.add_argument("--out", default="dist")
    args = ap.parse_args()
    return cmd_check(args) if args.cmd == "check" else cmd_build(args)


if __name__ == "__main__":
    sys.exit(main())
