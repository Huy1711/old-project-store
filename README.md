# Old project store

Our old app keeps each user's projects as JSON documents in a CouchDB-style document store, one database per user, named `user<id>` (for example `user12` holds user 12's documents).

## Run it

Python 3, nothing to install:

```
python3 store.py          # http://localhost:5984 (PORT=... to change)
```

## API

| Request | Returns |
|---|---|
| `GET /_all_dbs` | `["user12", ...]` |
| `GET /{db}` | `{"db_name", "doc_count", "update_seq"}` |
| `GET /{db}/_all_docs` | `{"total_rows", "rows": [{"id", "key", "value": {"rev"}}]}`, sorted by id. Options: `include_docs=true`, `limit`, `skip`, `keys=<JSON list>` |
| `POST /{db}/_all_docs` with `{"keys": [...]}` | same as above, keys in the body. A key that was deleted returns `"value": {"deleted": true}` and `"doc": null`; a key that never existed returns `"error": "not_found"` |
| `GET /{db}/_changes?since=<seq>` | `{"results": [{"seq", "id", "deleted"?, "doc"?}], "last_seq"}`. Options: `limit`, `include_docs=true` |
| `GET /{db}/{doc id}` | one document (URL-encode the id) |

The store may answer `429 Too Many Requests` with a `Retry-After` header.

### Sequences and the change feed

- Every write to a database (create, edit or delete of a document) gets the next number in that database's sequence. `update_seq` is the sequence of the latest write.
- `_changes?since=N` lists the documents written after sequence `N`, oldest first. Each document appears once, at its latest write, with `"deleted": true` if that write was a delete. `since=0` lists every document the database has ever held.
- `last_seq` is the sequence of the last result. Pass it as `since` to continue from there.
- Treat sequences as opaque values: store them and pass them back, don't do arithmetic on them. (They are integers here; in real CouchDB they are strings.)
- `_rev` is a document's revision, `<N>-<hash>`: `N` counts the document's writes, the hash fingerprints its content. Same `_rev`, same content.

### Field names

- Inside a document, the store's own fields start with `_` (`_id`, `_rev`), so they never clash with the app's fields (cameras have their own `id`).
- Listings (`_all_docs`, `_changes`) describe documents rather than being documents, so they use plain names: a row's `id` is the document's `_id`, and `value.rev` / `changes[].rev` is its `_rev`.
- `_all_docs` is a built-in view sorted by id, so each row's `key` is the id too. A requested key that doesn't exist comes back as `{"key", "error"}`, with no `id`.

## Documents

Each user database holds that user's **project** documents and one **settings** document.

### Project

```jsonc
{
  "_id": "8f14e45f-...",            // uuid, the project's identity
  "_rev": "3-a1b2...",
  "type": "project",
  "title": "Warehouse North 4",
  "createdAt": 1612345678901,       // epoch milliseconds
  "updatedAt": 1623456789012,       // epoch milliseconds
  "units": "imperial",              // "imperial" (feet) or "metric" (metres)
  "cameras": [
    {
      "id": 1,                      // unique within the project
      "name": "Camera 1",
      "model": "Axis P3265-LVE",
      "lat": 40.123456,
      "lng": -73.654321,
      "distance": 32.5,             // distance to target, in the project's units
      "height": 9.8,                // mounting height, in the project's units
      "fov": 90                     // horizontal field of view, degrees
    }
  ],
  "ui": { "showCameraBlindspot": true, "zoom": 18 }   // view options
}
```

### Settings

```jsonc
{
  "_id": "c9f0f895-...",
  "_rev": "1-...",
  "type": "settings",
  "favorites": ["Axis P3265-LVE", "Verkada CD62"],   // favourite camera models
  "units": "imperial"                                  // default for new projects
}
```

## Example: one user's database

User 12 has two projects and their settings. Over time they created project `a1f0…`, then `b7c2…`, saved their settings, edited `a1f0…`, created `d93e…` and later deleted it, and edited `b7c2…`. That is 7 writes, so `update_seq` is 7.

`GET /user12`

```json
{"db_name": "user12", "doc_count": 3, "update_seq": 7}
```

`GET /user12/_all_docs` lists the documents that exist now (the deleted one is not there), sorted by id:

```json
{
  "total_rows": 3,
  "rows": [
    {"id": "a1f0c3d2-...", "key": "a1f0c3d2-...", "value": {"rev": "2-9c4e..."}},
    {"id": "b7c21e8a-...", "key": "b7c21e8a-...", "value": {"rev": "2-71ab..."}},
    {"id": "c5d8f017-...", "key": "c5d8f017-...", "value": {"rev": "1-0e3f..."}}
  ]
}
```

`GET /user12/_changes?since=0` lists each document once, at its latest write, oldest first:

```json
{
  "results": [
    {"seq": 3, "id": "c5d8f017-...", "changes": [{"rev": "1-0e3f..."}]},
    {"seq": 4, "id": "a1f0c3d2-...", "changes": [{"rev": "2-9c4e..."}]},
    {"seq": 6, "id": "d93e5b40-...", "changes": [{"rev": "2-deleted"}], "deleted": true},
    {"seq": 7, "id": "b7c21e8a-...", "changes": [{"rev": "2-71ab..."}]}
  ],
  "last_seq": 7
}
```

`GET /user12/_changes?since=4` returns only the last two (seq 6 and 7), with `"last_seq": 7`. With `include_docs=true`, each result also carries `"doc"`: the document, or `null` for a delete.

`GET /user12/a1f0c3d2-...` returns the project document itself, in the shape shown above.
