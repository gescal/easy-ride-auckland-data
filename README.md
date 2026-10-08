# Easy Ride Auckland — timetable data

Timetable and stop data for the **Easy Ride Auckland｜奧克蘭輕鬆搭** iPhone app.

A GitHub Action runs every day. When Auckland Transport publishes a new GTFS feed it rebuilds the data and publishes it to GitHub Pages:

- `v1/manifest.json`: version, feed validity dates, file hashes
- `v1/timetable.sqlite.deflate`: scheduled departures for every stop
- `v1/transit.json.deflate`: stops, routes and route shapes

The app checks the manifest and downloads new files when the feed changes, so the timetable never goes stale.

## Data licence

Contains data from [Auckland Transport](https://at.govt.nz/about-us/at-data-sources/), licensed under [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). The GTFS feed has been converted (merged stops, compressed timetable) for use in the app; it is not endorsed by Auckland Transport.

Run it by hand:

```
curl -fsSL -o gtfs.zip https://gtfs.at.govt.nz/gtfs.zip
python3 tools/publish_data.py build --gtfs gtfs.zip --legacy source/auckland-map.json --out dist
```
