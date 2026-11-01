# Location from a tracking app (v1)

Muninn reads where the user is from one thing, a **location point** (`src/muninn/core/location.py`):

| Field | |
|---|---|
| `ts` | when the device was there (timezone-aware) |
| `lat`, `lon` | WGS 84 degrees |
| `device` | which of the user's devices (a short name the app gives) |
| `accuracy_m` | radius it may be off by, in metres; optional (taken as 50) |

Points are kept as sensitive `location` observations, one per device and second (a point posted again is one point),
with the name of the app that sent it.

## A tracking app

Each app is one implementation of `LocationApp` (`src/muninn/core/ports.py`), registered in `LOCATION_APPS`
(`src/muninn/main.py`):

- `name`: the app posts to `POST /api/ingest/location/<name>`, with the ingest token as a Bearer token, or as the
  password of HTTP Basic (what phone trackers can send). OwnTracks phones set up earlier post to
  `/api/ingest/owntracks`, which is the same.
- `points(body)`: the points in one post (none for what is not a location; that is never refused, so the app does not
  resend it).
- `reply()`: the JSON body the app expects back.
- `setup(url, token, words)`: how the user sets the app up: a link that opens the app configured, when it takes one, and the
  fields to type in by hand, said in the language Muninn speaks (`words`). The settings page shows these for every registered app (`GET /api/setup/location`).

Adding an app is one module under `src/muninn/adapters/sources/location/` and one entry in `LOCATION_APPS`.
