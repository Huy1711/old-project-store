#!/usr/bin/env python3
"""The old app's document store (CouchDB-shaped), read-only. Python stdlib only.

  python store.py        # serves http://localhost:5984 (PORT to change)

Serves data.json.gz next to this file: each database's documents and change history.

API:
  GET  /_all_dbs                                   ["user12", ...], one database per user
  GET  /{db}                                       {"db_name", "doc_count", "update_seq"}
  GET  /{db}/_all_docs[?include_docs=true&limit=&skip=&keys=<json list>]
  POST /{db}/_all_docs  {"keys": [...]}            same, keys in the body
  GET  /{db}/_changes?since=<seq>[&limit=&include_docs=true]
                                                   {"results": [{"seq", "id", "deleted"?, "doc"?}], "last_seq"}
  GET  /{db}/{doc id}                              one document (ids are URL-encoded)
"""
import gzip
import json
import os
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

CHAOS_429 = float(os.environ.get("CHAOS_429", "0.03"))
LATENCY = float(os.environ.get("LATENCY_MS", "30")) / 1000
DOC_LATENCY = float(os.environ.get("DOC_LATENCY_MS", "10")) / 1000
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json.gz")


class Database:
    def __init__(self, data):
        self.seq = data["update_seq"]
        self.docs = {row["doc"]["_id"]: row["doc"] for row in data["rows"]}
        self.deleted_revs = {row["id"]: row["rev"] for row in data["deleted"]}
        # Each document's latest change, as CouchDB keeps it: id -> (seq, deleted)
        self.changes = {row["doc"]["_id"]: (row["seq"], False) for row in data["rows"]}
        self.changes.update({row["id"]: (row["seq"], True) for row in data["deleted"]})

    def rev(self, doc_id):
        return self.docs[doc_id]["_rev"] if doc_id in self.docs else self.deleted_revs[doc_id]


with gzip.open(DATA, "rt") as f:
    DATABASES = {name: Database(data) for name, data in json.load(f).items()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("VERBOSE"):
            super().log_message(fmt, *args)

    def send_json(self, status, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        parts = urlsplit(self.path)
        segments = parts.path.lstrip("/").split("/", 1)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        return unquote(segments[0]), (segments[1] if len(segments) > 1 else ""), query

    def throttled(self):
        time.sleep(LATENCY)
        if random.random() < CHAOS_429:
            self.send_json(429, {"error": "too_many_requests", "reason": "rate limit exceeded"}, {"Retry-After": "1"})
            return True
        return False

    def do_GET(self):
        if self.throttled():
            return
        db_name, rest, query = self.route()
        if db_name == "_all_dbs":
            return self.send_json(200, sorted(DATABASES))
        db = DATABASES.get(db_name)
        if db is None:
            return self.send_json(404, {"error": "not_found", "reason": "Database does not exist."})
        if rest == "":
            return self.send_json(200, {"db_name": db_name, "doc_count": len(db.docs), "update_seq": db.seq})
        if rest == "_all_docs":
            keys = json.loads(query["keys"]) if "keys" in query else None
            return self.all_docs(db, query, keys)
        if rest == "_changes":
            return self.changes(db, query)
        doc = db.docs.get(unquote(rest))
        if doc is None:
            return self.send_json(404, {"error": "not_found", "reason": "missing"})
        time.sleep(DOC_LATENCY)
        return self.send_json(200, doc)

    def do_POST(self):
        if self.throttled():
            return
        db_name, rest, query = self.route()
        db = DATABASES.get(db_name)
        if db is None or rest != "_all_docs":
            return self.send_json(404, {"error": "not_found"})
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else {}
        return self.all_docs(db, query, body.get("keys"))

    def all_docs(self, db, query, keys):
        include = query.get("include_docs") == "true"
        if keys is None:
            ids = sorted(db.docs)
            skip = int(query.get("skip", 0))
            ids = ids[skip:skip + int(query.get("limit", len(ids)))]
            rows = [{"id": i, "key": i, "value": {"rev": db.rev(i)}} for i in ids]
            if include:
                for row in rows:
                    row["doc"] = db.docs[row["id"]]
        else:
            rows = []
            for key in keys:
                if key in db.docs:
                    row = {"id": key, "key": key, "value": {"rev": db.rev(key)}}
                    if include:
                        row["doc"] = db.docs[key]
                elif key in db.changes:
                    row = {"id": key, "key": key, "value": {"rev": db.rev(key), "deleted": True}}
                    if include:
                        row["doc"] = None
                else:
                    row = {"key": key, "error": "not_found"}
                rows.append(row)
        if include:
            time.sleep(DOC_LATENCY * sum(1 for r in rows if r.get("doc")))
        return self.send_json(200, {"total_rows": len(db.docs), "rows": rows})

    def changes(self, db, query):
        since = int(query.get("since", 0) or 0)
        include = query.get("include_docs") == "true"
        pending = sorted((seq, doc_id, deleted) for doc_id, (seq, deleted) in db.changes.items() if seq > since)
        if "limit" in query:
            pending = pending[:int(query["limit"])]
        results = []
        for seq, doc_id, deleted in pending:
            change = {"seq": seq, "id": doc_id, "changes": [{"rev": db.rev(doc_id)}]}
            if deleted:
                change["deleted"] = True
            if include:
                change["doc"] = None if deleted else db.docs[doc_id]
            results.append(change)
        last_seq = results[-1]["seq"] if results else db.seq
        if include:
            time.sleep(DOC_LATENCY * sum(1 for c in results if c.get("doc")))
        return self.send_json(200, {"results": results, "last_seq": last_seq})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5984"))
    print(f"old store on http://localhost:{port}: {len(DATABASES)} databases", flush=True)
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), port), Handler).serve_forever()
