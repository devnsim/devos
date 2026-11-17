# ==============================================================================
# FILE: examples/engines/sample_data/engine.py
# OVERVIEW: Sample data engine - an example engine package built outside DevOS's engines/ folder: it serves a
#           made-up table of orders through the view source contract and streams new orders, so you can try
#           views, docking and the conformance kit without touching your own data.
# PURPOSE:
#   1. Source contract: collections, schema, query (Query v1 run by the shared table toolkit).
#   2. ticker: a stream of new orders (shows streams, cancel and back-pressure).
#   3. Read-only and deterministic (seeded), so its answers are the same everywhere.
# CONTRACT:
#   - Requires: engines.kernel, engines.toolkit.table (what every engine may use). Nothing else from DevOS.
#   - Exposes: SampleDataEngine (MANIFEST id "sample_data"), make_orders(count)
# NOTES:
#   - [2026-11-17] Created in Phase 3 of the engine plan (the docking example).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - order: One made-up row: id, title ("Order 1001"), customer, region, status, amount (USD), ordered (date).
# - ORDER_COUNT (int): Orders in the sample (default: 240).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. SAMPLE_DATA
#    1.1 data
#    1.2 engine
# ==============================================================================

# ==============================================================================
# [1.1] data
# ==============================================================================
from __future__ import annotations

import datetime as dt
import random
import time

from engines.kernel import Engine, EngineError, EngineManifest, Job, Reply, RequestSpec, shapes
from engines.toolkit.table import run_query

ORDER_COUNT = 240
REGIONS = ("North", "South", "East", "West")
STATUSES = ("Open", "Shipped", "Returned")
CUSTOMERS = ("Avery Labs", "Birch & Co", "Cobalt Works", "Dune Studio", "Elm Partners", "Fable Foods", "Granite Tools")
FIELDS = [
    {"key": "customer", "title": "Customer", "kind": "text", "options": [], "unit": ""},
    {"key": "region", "title": "Region", "kind": "select", "options": list(REGIONS), "unit": ""},
    {"key": "status", "title": "Status", "kind": "select", "options": list(STATUSES), "unit": ""},
    {"key": "amount", "title": "Amount", "kind": "number", "options": [], "unit": "USD"},
    {"key": "ordered", "title": "Ordered", "kind": "date", "options": [], "unit": ""},
]
COLLECTION = {"collection": "orders", "title": "Sample orders", "noun": "order"}


def make_orders(count: int, seed: int = 7, first_id: int = 1001) -> list[dict]:
    rng = random.Random(seed)
    start = dt.date(2026, 1, 1)
    return [{"id": first_id + index, "title": f"Order {first_id + index}", "customer": rng.choice(CUSTOMERS),
             "region": rng.choice(REGIONS), "status": rng.choices(STATUSES, (6, 10, 1))[0],
             "amount": round(rng.uniform(40, 2400), 2), "ordered": (start + dt.timedelta(days=rng.randrange(180))).isoformat()}
            for index in range(count)]
# --- END [1.1] ---


# ==============================================================================
# [1.2] engine
# ==============================================================================
class SampleDataEngine(Engine):
    MANIFEST = EngineManifest(
        "sample_data", "Sample data", "1.0", "Made-up orders to try views and docking with.",
        requests=(
            RequestSpec("collections", "The sample tables", example={}),
            RequestSpec("schema", "A sample table's fields", params=("collection",), example={"collection": "orders"}),
            RequestSpec("query", "Rows of a sample table", params=("collection",), optional=("query",), shape="table",
                        example={"collection": "orders", "query": {"limit": 10}}),
            RequestSpec("ticker", "New made-up orders, one every interval", optional=("count", "interval_s"),
                        streams=True, example={"count": 3, "interval_s": 0.01}),
        ),
        shapes=("table",),
    )

    def start(self) -> None:
        self.orders = make_orders(ORDER_COUNT)

    def _check(self, payload: dict) -> None:
        if payload["collection"] != "orders":
            raise EngineError(f"There is no sample table “{payload['collection']}”", code="not_found")

    def on_collections(self, _payload: dict, _job: Job) -> dict:
        return {"collections": [{**COLLECTION, "count": len(self.orders)}]}

    def on_schema(self, payload: dict, _job: Job) -> dict:
        self._check(payload)
        return {"fields": FIELDS, "title_key": "title", "noun": "order"}

    def on_query(self, payload: dict, _job: Job) -> Reply:
        self._check(payload)
        question = payload.get("query") or {}
        rows, total, fields = run_query(self.orders, FIELDS, question)
        shape, attachments = shapes.table(fields, rows, total=total, offset=question.get("offset", 0),
                                          source={"engine": "sample_data", "collection": "orders", "noun": "order"})
        return Reply(shape, attachments)

    def on_ticker(self, payload: dict, job: Job):
        count, interval = int(payload.get("count", 10)), float(payload.get("interval_s", 0.5))
        for order in make_orders(count, seed=int(time.time()), first_id=9000):
            if job.is_cancelled():
                return
            time.sleep(interval)
            yield {"order": order}
# --- END [1.2] ---
