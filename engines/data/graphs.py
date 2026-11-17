# ==============================================================================
# FILE: engines/data/graphs.py
# OVERVIEW: Kùzu graph databases (Library kind "graph") in the Data engine: every node table is a collection
#           "graph:<item id>/<table>" (rows keyed by the primary key), and graph_shape() draws the database as
#           a Graph shape (nodes and relationships). Filters, sort and paging run in Kùzu as Cypher.
# PURPOSE:
#   1. NodeTable: fields from the table's columns; query (Cypher WHERE / ORDER BY / SKIP / LIMIT; grouping
#      and list columns in memory); get, create (needs the primary key unless it is SERIAL), update (not the
#      key), delete (with its relationships), add property.
#   2. graph_shape(db, limit): a sample of nodes and relationships as a Graph shape.
# CONTRACT:
#   - Requires: kuzu (engines.toolkit.kuzu_db.GraphDb), engines.data.common, engines.toolkit.table, engines.kernel.
#   - Exposes: NodeTable(ref, db, title), graph_shape(db, limit), kind_for_kuzu(type_name), KUZU_FOR_KIND
# NOTES:
#   - [2026-11-17] Phase 5b: cypher() for the Cypher console, structure(); graph nodes carry their table key (row_id).
#   - [2026-11-17] Created in Phase 5a of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - KUZU_FOR_KIND (dict): Field kind -> the Kùzu type add_property declares.
# - MEMORY_LIMIT (int): Rows read when a question has to run in memory (default: 200 000).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. GRAPHS
#    1.1 types
#    1.2 cypher
#    1.3 node_table
#    1.4 graph_shape
# ==============================================================================

# ==============================================================================
# [1.1] types
# ==============================================================================
from __future__ import annotations

import datetime as dt

from engines.data.common import FileRef, Source, coerce, diff, field
from engines.kernel import EngineError, shapes
from engines.kernel.query import validate_query
from engines.toolkit.kuzu_db import GraphDb, GraphDbError, element_key, properties_of, quote_name
from engines.toolkit.table import run_query

KUZU_FOR_KIND = {"text": "STRING", "long_text": "STRING", "select": "STRING", "url": "STRING", "number": "DOUBLE",
                 "integer": "INT64", "checkbox": "BOOLEAN", "date": "DATE", "datetime": "TIMESTAMP"}
MEMORY_LIMIT = 200_000


def kind_for_kuzu(type_name: str) -> str:
    upper = type_name.upper()
    if upper.endswith("[]") or upper.startswith(("LIST", "STRUCT", "MAP")):
        return "json"
    if upper in ("STRING", "UUID"):
        return "text"
    if "INT" in upper or upper == "SERIAL":
        return "integer"
    if upper in ("DOUBLE", "FLOAT") or upper.startswith("DECIMAL"):
        return "number"
    return {"BOOLEAN": "checkbox", "DATE": "date", "TIMESTAMP": "datetime"}.get(upper, "json")


def _plain(value):
    if isinstance(value, dt.datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return value
    return value if isinstance(value, (int, float, str, bool)) or value is None else str(value)


def _kuzu_value(kind: str, value):
    if value is None:
        return None
    if kind == "date":
        return dt.date.fromisoformat(str(value)[:10])
    if kind == "datetime":
        return dt.datetime.fromisoformat(str(value))
    return value
# --- END [1.1] ---


# ==============================================================================
# [1.2] cypher
# ==============================================================================
class _Cypher:
    """A Query v1 filter tree as a Cypher condition on node `n`, with $parameters."""

    def __init__(self, kinds: dict[str, str]) -> None:
        self.kinds, self.params = kinds, {}

    def param(self, value, kind: str) -> str:
        name = f"p{len(self.params)}"
        if kind in ("number", "integer") and value is not None:
            try:
                value = float(value) if kind == "number" else int(float(value))
            except (TypeError, ValueError):
                raise EngineError(f"“{value}” is not a number", code="bad_query") from None
        elif kind in ("text", "long_text", "select", "url"):
            value = str(value).casefold()
        else:
            value = _kuzu_value(kind, value)
        self.params[name] = value
        return f"${name}"

    def node(self, node) -> str:
        if not node:
            return "true"
        if "all" in node:
            return "(" + " AND ".join([self.node(child) for child in node["all"]] or ["true"]) + ")"
        if "any" in node:
            return "(" + " OR ".join([self.node(child) for child in node["any"]] or ["false"]) + ")"
        if "not" in node:
            return f"NOT {self.node(node['not'])}"
        key, op, value = node["field"], node["op"], node.get("value")
        kind = self.kinds[key]
        text = kind in ("text", "long_text", "select", "url")
        expr = f"lower(n.{quote_name(key)})" if text else f"n.{quote_name(key)}"
        empty = f"(n.{quote_name(key)} IS NULL" + (f" OR n.{quote_name(key)} = ''" if text else "") + ")"
        if op == "empty":
            return empty
        if op == "not_empty":
            return f"NOT {empty}"
        if op in ("in", "not_in"):
            items = "[" + ", ".join(self.param(item, kind) for item in value or []) + "]"
            return f"{expr} IN {items}" if op == "in" else f"({empty} OR NOT {expr} IN {items})"
        if op == "between":
            parts = [f"NOT {empty}"] + [f"{expr} {symbol} {self.param(bound, kind)}"
                                        for bound, symbol in zip(value, (">=", "<=")) if bound is not None]
            return "(" + " AND ".join(parts) + ")"
        if op == "contains":
            return f"{expr} CONTAINS {self.param(value, 'text')}"
        if op == "starts_with":
            return f"starts_with({expr}, {self.param(value, 'text')})"
        if op == "ne":
            return f"({empty} OR {expr} <> {self.param(value, kind)})"
        symbol = {"eq": "=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}[op]
        return f"{expr} {symbol} {self.param(value, kind)}"
# --- END [1.2] ---


# ==============================================================================
# [1.3] node_table
# ==============================================================================
class NodeTable(Source):
    noun = "node"

    def __init__(self, ref: FileRef, db: GraphDb, title: str) -> None:
        super().__init__(ref.collection)
        self.ref, self.db, self.title = ref, db, title
        try:
            if ref.table not in db.node_tables():
                raise EngineError(f"{title}: there is no node table “{ref.table}”", code="not_found")
            self.columns = db.columns(ref.table)
        except GraphDbError as error:
            raise EngineError(f"{title}: {error}", code="database_error") from None
        self.table = quote_name(ref.table)
        self.kinds = {name: kind_for_kuzu(type_name) for name, type_name, _pk in self.columns}
        self.types = {name: type_name for name, type_name, _pk in self.columns}
        self.key = next((name for name, _type, pk in self.columns if pk), self.columns[0][0])
        self.title_key = self.key if self.kinds[self.key] == "text" else \
            next((name for name in self.kinds if self.kinds[name] == "text"), self.key)

    def fields(self) -> list[dict]:
        return [field(name, name, self.kinds[name], primary_key=name == self.key,
                      read_only=name == self.key or self.kinds[name] == "json") for name, *_rest in self.columns]

    def _run(self, statement: str, params: dict | None = None) -> list[list]:
        try:
            return self.db.query(statement, params or {})
        except GraphDbError as error:
            raise EngineError(f"{self.title}: {error}", code="database_error") from None

    def _row(self, values: list) -> dict:
        return {name: _plain(value) for (name, *_rest), value in zip(self.columns, values)}

    def _select(self) -> str:
        return ", ".join(f"n.{quote_name(name)}" for name, *_rest in self.columns)

    def query(self, question: dict) -> tuple[list[dict], int, list[dict]]:
        fields = self.fields()
        problems = validate_query(question, set(self.kinds))
        if problems:
            raise EngineError(f"Bad query: {'; '.join(problems)}", code="bad_query")
        named = {item.get("field") for item in question.get("sort") or []}
        if question.get("group_by") or question.get("time_range") or \
                any(self.kinds.get(key) == "json" for key in named | _filter_fields(question.get("filter"))):
            rows = [self._row(values) for values in self._run(
                f"MATCH (n:{self.table}) RETURN {self._select()} LIMIT {MEMORY_LIMIT}")]
            return run_query(rows, fields, question, self.title_key, self.key)
        cypher = _Cypher(self.kinds)
        where = cypher.node(question.get("filter"))
        if question.get("search"):
            texts = [name for name, kind in self.kinds.items() if kind in ("text", "long_text")]
            needle = cypher.param(question["search"], "text")
            where += " AND (" + (" OR ".join(f"lower(n.{quote_name(name)}) CONTAINS {needle}" for name in texts) or
                                 "false") + ")"
        total = int(self._run(f"MATCH (n:{self.table}) WHERE {where} RETURN count(*)", cypher.params)[0][0])
        order = ", ".join(f"n.{quote_name(item['field'])}{' DESC' if item.get('descending') else ''}"
                          for item in question.get("sort") or []) or f"n.{quote_name(self.key)}"
        offset, limit = int(question.get("offset") or 0), question.get("limit")
        statement = f"MATCH (n:{self.table}) WHERE {where} RETURN {self._select()} ORDER BY {order} SKIP {offset}"
        if limit is not None:
            statement += f" LIMIT {int(limit)}"
        rows = [self._row(values) for values in self._run(statement, cypher.params)] if limit != 0 else []
        if question.get("fields"):
            keep = set(question["fields"]) | {self.key, self.title_key}
            rows = [{key: value for key, value in row.items() if key in keep} for row in rows]
            fields = [item for item in fields if item["key"] in keep]
        return rows, total, fields

    def get(self, row_id) -> dict:
        found = self._run(f"MATCH (n:{self.table}) WHERE n.{quote_name(self.key)} = $id RETURN {self._select()}",
                          {"id": _kuzu_value(self.kinds[self.key], row_id)})
        if not found:
            raise EngineError(f"{self.title}: there is no node {row_id}", code="not_found")
        return self._row(found[0])

    def _values(self, values: dict) -> dict:
        clean = {}
        for key, value in values.items():
            if key not in self.kinds:
                raise EngineError(f"{self.title}: there is no property “{key}”", code="invalid_value")
            clean[key] = _kuzu_value(self.kinds[key], coerce(self.kinds[key], value, key))
        return clean

    def update(self, row_id, values: dict) -> tuple[dict, dict]:
        before = self.get(row_id)
        if self.key in values and values[self.key] != before[self.key]:
            raise self.refuse(f"“{self.key}” identifies the node", "create a new node instead")
        clean = {key: value for key, value in self._values(values).items() if key != self.key}
        if clean:
            sets = ", ".join(f"n.{quote_name(key)} = $v{index}" for index, key in enumerate(clean))
            params = {f"v{index}": value for index, value in enumerate(clean.values())} | \
                {"id": _kuzu_value(self.kinds[self.key], row_id)}
            self._run(f"MATCH (n:{self.table}) WHERE n.{quote_name(self.key)} = $id SET {sets}", params)
        after = self.get(row_id)
        return after, diff(before, after)

    def create(self, title: str | None, values: dict) -> dict:
        if title and self.title_key not in values:
            values = {self.title_key: title, **values}
        clean = self._values(values)
        serial = self.types[self.key].upper() == "SERIAL"
        if not serial and clean.get(self.key) in (None, ""):
            raise EngineError(f"{self.title}: a new node needs its “{self.key}”", code="invalid_value")
        clean = {key: value for key, value in clean.items() if value is not None and not (serial and key == self.key)}
        properties = ", ".join(f"{quote_name(key)}: $v{index}" for index, key in enumerate(clean))
        params = {f"v{index}": value for index, value in enumerate(clean.values())}
        if serial:
            found = self._run(f"CREATE (n:{self.table} {{{properties}}}) RETURN n.{quote_name(self.key)}", params)
            return self.get(found[0][0])
        self._run(f"CREATE (n:{self.table} {{{properties}}})", params)
        return self.get(clean[self.key])

    def delete(self, row_ids: list) -> list[str]:
        rows = [self.get(row_id) for row_id in row_ids]
        for row in rows:
            self._run(f"MATCH (n:{self.table}) WHERE n.{quote_name(self.key)} = $id DETACH DELETE n",
                      {"id": _kuzu_value(self.kinds[self.key], row[self.key])})
        return [str(row.get(self.title_key)) for row in rows]

    def structure(self) -> dict:
        try:
            relations = [{"name": name, "connects": [list(pair) for pair in self.db.connection_of(name)]}
                         for name in self.db.rel_tables()]
        except GraphDbError as error:
            raise EngineError(f"{self.title}: {error}", code="database_error") from None
        relations = [rel for rel in relations if any(self.ref.table in pair for pair in rel["connects"])]
        return {**super().structure(), "kind": "node table", "relationships": relations,
                "rows": int(self._run(f"MATCH (n:{self.table}) RETURN count(*)")[0][0])}

    def cypher(self, script: str, row_limit: int) -> tuple[list[dict], bool]:
        """Run Cypher on this table's database: (results, did it write); stops at the first error."""
        from engines.toolkit.kuzu_db import describe
        results, wrote = [], False
        for outcome in self.db.run(script):
            writes = outcome.statement.split()[0].upper() in ("CREATE", "DROP", "ALTER", "MERGE", "DELETE", "SET", "COPY")
            wrote = wrote or (writes and not outcome.error)
            results.append({"statement": outcome.statement, "columns": outcome.columns, "error": outcome.error,
                            "rows": [[describe(value) if isinstance(value, dict) else _plain(value) for value in row]
                                     for row in outcome.rows[:row_limit]],
                            "truncated": len(outcome.rows) > row_limit, "changed": 0, "writes": writes})
        return results, wrote

    def add_property(self, title: str, kind: str, options: tuple, formula: str) -> dict:
        if kind not in KUZU_FOR_KIND:
            raise self.refuse(f"a graph property can't be of kind “{kind}”")
        try:
            name = quote_name(title.strip())
        except GraphDbError as error:
            raise EngineError(f"{self.title}: {error}", code="invalid_value") from None
        self._run(f"ALTER TABLE {self.table} ADD {name} {KUZU_FOR_KIND[kind]}")
        self.columns.append((name, KUZU_FOR_KIND[kind], False))
        self.kinds[name], self.types[name] = kind_for_kuzu(KUZU_FOR_KIND[kind]), KUZU_FOR_KIND[kind]
        return field(name, name, self.kinds[name])


def _filter_fields(node) -> set:
    if not node:
        return set()
    if "all" in node or "any" in node:
        return set().union(*[_filter_fields(child) for child in node.get("all", node.get("any")) or []])
    if "not" in node:
        return _filter_fields(node["not"])
    return {node.get("field")}
# --- END [1.3] ---


# ==============================================================================
# [1.4] graph_shape
# ==============================================================================
def graph_shape(db: GraphDb, limit: int = 300) -> dict:
    """Nodes (id "Table:offset", title, label, properties) and relationships as a Graph shape."""
    try:
        sample = db.graph_sample(limit)
    except GraphDbError as error:
        raise EngineError(str(error), code="database_error") from None
    nodes, keys = [], {}
    for key, node in sample.nodes.items():
        props = {name: _plain(value) for name, value in properties_of(node).items()}
        title = next((str(value) for value in props.values() if isinstance(value, str)), key)
        if node["_label"] not in keys:                  # the table's primary key: the node's row id in a table view
            keys[node["_label"]] = next((name for name, _type, pk in db.columns(node["_label"]) if pk), "")
        nodes.append({"id": key, "title": title, "label": node["_label"], "row_id": props.get(keys[node["_label"]]),
                      "properties": props})
    edges = [{"id": f"{element_key(rel['_src'])}-{rel['_label']}-{element_key(rel['_dst'])}",
              "source": element_key(rel["_src"]), "target": element_key(rel["_dst"]), "label": rel["_label"],
              "properties": {name: _plain(value) for name, value in properties_of(rel).items()}}
             for rel in sample.rels]
    node_fields = [field("title", "Name", "text"), field("label", "Table", "select"), field("row_id", "Key", "text"),
                   field("properties", "Properties", "json")]
    edge_fields = [field("label", "Relationship", "select"), field("properties", "Properties", "json")]
    graph, _attachments = shapes.graph(node_fields, nodes, edge_fields, edges)
    return graph
# --- END [1.4] ---
