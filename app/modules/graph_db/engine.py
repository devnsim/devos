# ==============================================================================
# FILE: app/modules/graph_db/engine.py
# OVERVIEW: Graph database logic (no Qt) on Kùzu, an embedded property-graph database queried with
#           Cypher: open a database file, run queries, read the schema, create tables, sample a graph
#           for drawing, load a small sample dataset.
# PURPOSE:
#   1. GraphDb(path): run(query) -> list[QueryOutcome] (several statements allowed); schema helpers
#      (node_tables, rel_tables, columns, counts); create / drop tables; graph_sample() for the view.
#   2. Value helpers: is_node(), is_rel(), element_key(), describe() (a node / rel as short text).
#   3. kuzu_unavailable_reason(): None when the kuzu package imports.
# CONTRACT:
#   - Requires: kuzu (optional; module shows the reason when missing). No Qt.
#   - Exposes: GraphDb, GraphDbError, QueryOutcome, GraphSample, kuzu_unavailable_reason(), is_node(), is_rel(),
#              element_key(), describe(), KUZU_TYPES, quote_name()
# NOTES:
#   - [2026-11-16] Created with the Graph Database module (Kùzu 0.11: one database file + a .wal next to it).
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - node table / rel table: Kùzu's typed tables of nodes and of relationships (FROM one node table TO another).
# - element: A node or a relationship value returned by a query (a dict with _id, _label ...).
# - element_key (str): "table:offset" from an element's _id; stable within a database.
# - graph sample: Nodes and relationships fetched for drawing (bounded by a limit).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. ENGINE
#    1.1 imports_and_types
#    1.2 values
#    1.3 graph_db
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_types
# ==============================================================================
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

KUZU_TYPES = ("STRING", "INT64", "INT32", "DOUBLE", "FLOAT", "BOOLEAN", "DATE", "TIMESTAMP", "INTERVAL", "BLOB", "UUID",
              "SERIAL", "STRING[]", "INT64[]", "DOUBLE[]")


class GraphDbError(Exception):
    pass


@dataclass
class QueryOutcome:
    statement: str
    columns: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str = ""


@dataclass
class GraphSample:
    nodes: dict[str, dict] = field(default_factory=dict)          # element_key -> node
    rels: list[dict] = field(default_factory=list)


def kuzu_unavailable_reason() -> str | None:
    try:
        import kuzu  # noqa: F401
        return None
    except ImportError as error:
        return f"The kuzu package isn't installed ({error}). It's in shell.nix — re-enter nix-shell."
# --- END [1.1] ---

# ==============================================================================
# [1.2] values
# ==============================================================================
def is_node(value) -> bool:
    return isinstance(value, dict) and "_label" in value and "_id" in value and "_src" not in value


def is_rel(value) -> bool:
    return isinstance(value, dict) and "_src" in value and "_dst" in value


def element_key(identifier: dict) -> str:
    return f"{identifier['table']}:{identifier['offset']}"


def properties_of(element: dict) -> dict:
    return {key: value for key, value in element.items() if not key.startswith("_") and value is not None}


def describe(value) -> str:
    """A node, rel, list or plain value as short display text."""
    if is_node(value):
        props = properties_of(value)
        return f"(:{value['_label']} {{{', '.join(f'{k}: {v}' for k, v in list(props.items())[:3])}}})"
    if is_rel(value):
        props = properties_of(value)
        return f"[:{value['_label']}{' ' + str(props) if props else ''}]"
    if isinstance(value, dict) and "_nodes" in value:                  # a path
        return f"path of {len(value.get('_rels', []))} relationships"
    if value is None:
        return "NULL"
    return str(value)


def quote_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise GraphDbError(f"“{name}” isn't a valid name (letters, digits, _; not starting with a digit)")
    return name


def split_statements(script: str) -> list[str]:
    """Split on ; outside quotes (Cypher strings use ' or ")."""
    statements, current, quote = [], [], None
    for char in script:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char == ";":
            statements.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    statements.append("".join(current).strip())
    return [statement for statement in statements if statement]
# --- END [1.2] ---

# ==============================================================================
# [1.3] graph_db
# ==============================================================================
class GraphDb:
    def __init__(self, path: str | Path) -> None:
        import kuzu
        self.path = Path(path)
        try:
            self.database = kuzu.Database(str(self.path))
            self.connection = kuzu.Connection(self.database)
        except RuntimeError as error:
            raise GraphDbError(f"can't open the graph database: {error}") from error

    def close(self) -> None:
        self.connection.close()
        self.database.close()

    def run(self, script: str) -> list[QueryOutcome]:
        """Run each statement; stops at the first error (that outcome carries the message)."""
        outcomes = []
        for statement in split_statements(script):
            outcome = QueryOutcome(statement)
            started = time.perf_counter()
            try:
                result = self.connection.execute(statement)
                outcome.columns = result.get_column_names()
                outcome.types = result.get_column_data_types()
                while result.has_next():
                    outcome.rows.append(result.get_next())
            except RuntimeError as error:
                outcome.error = str(error).strip()
            outcome.elapsed_ms = (time.perf_counter() - started) * 1000
            outcomes.append(outcome)
            if outcome.error:
                break
        return outcomes

    def query(self, statement: str, parameters: dict | None = None) -> list[list]:
        try:
            result = self.connection.execute(statement, parameters or {})
        except RuntimeError as error:
            raise GraphDbError(str(error).strip()) from error
        rows = []
        while result.has_next():
            rows.append(result.get_next())
        return rows

    def tables(self) -> list[tuple[str, str]]:
        """(name, "NODE" | "REL") for every table."""
        return [(row[1], row[2]) for row in self.query("CALL show_tables() RETURN *")]

    def node_tables(self) -> list[str]:
        return [name for name, kind in self.tables() if kind == "NODE"]

    def rel_tables(self) -> list[str]:
        return [name for name, kind in self.tables() if kind == "REL"]

    def columns(self, table: str) -> list[tuple[str, str, bool]]:
        """(name, type, is_primary_key)."""
        return [(row[1], row[2], bool(row[4]) if len(row) > 4 and isinstance(row[4], bool) else False)
                for row in self.query(f"CALL table_info('{quote_name(table)}') RETURN *")]

    def connection_of(self, rel_table: str) -> list[tuple[str, str]]:
        return [(row[0], row[1]) for row in self.query(f"CALL show_connection('{quote_name(rel_table)}') RETURN *")]

    def count(self, table: str, kind: str) -> int:
        pattern = f"(n:{quote_name(table)})" if kind == "NODE" else f"()-[n:{quote_name(table)}]->()"
        return int(self.query(f"MATCH {pattern} RETURN count(*)")[0][0])

    def create_node_table(self, name: str, columns: list[tuple[str, str]], primary_key: str) -> None:
        if not columns:
            raise GraphDbError("a node table needs at least one property (its primary key)")
        definition = ", ".join(f"{quote_name(column)} {kind}" for column, kind in columns)
        self.query(f"CREATE NODE TABLE {quote_name(name)}({definition}, PRIMARY KEY({quote_name(primary_key)}))")

    def create_rel_table(self, name: str, source: str, target: str, columns: list[tuple[str, str]]) -> None:
        extra = "".join(f", {quote_name(column)} {kind}" for column, kind in columns)
        self.query(f"CREATE REL TABLE {quote_name(name)}(FROM {quote_name(source)} TO {quote_name(target)}{extra})")

    def drop_table(self, name: str) -> None:
        self.query(f"DROP TABLE {quote_name(name)}")

    def graph_sample(self, limit: int = 300) -> GraphSample:
        """Relationships (with their end nodes) plus unconnected nodes, up to `limit` of each."""
        sample = GraphSample()
        for source, rel, target in self.query(f"MATCH (a)-[r]->(b) RETURN a, r, b LIMIT {int(limit)}"):
            sample.nodes[element_key(source["_id"])] = source
            sample.nodes[element_key(target["_id"])] = target
            sample.rels.append(rel)
        for (node,) in self.query(f"MATCH (n) RETURN n LIMIT {int(limit)}"):
            sample.nodes.setdefault(element_key(node["_id"]), node)
        return sample

    def load_sample_data(self) -> None:
        """A small people / cities / projects graph to explore."""
        script = """
        CREATE NODE TABLE IF NOT EXISTS Person(name STRING, role STRING, PRIMARY KEY(name));
        CREATE NODE TABLE IF NOT EXISTS City(name STRING, country STRING, PRIMARY KEY(name));
        CREATE NODE TABLE IF NOT EXISTS Project(name STRING, status STRING, PRIMARY KEY(name));
        CREATE REL TABLE IF NOT EXISTS Knows(FROM Person TO Person, since INT64);
        CREATE REL TABLE IF NOT EXISTS LivesIn(FROM Person TO City);
        CREATE REL TABLE IF NOT EXISTS WorksOn(FROM Person TO Project, hours INT64);
        """
        for outcome in self.run(script):
            if outcome.error:
                raise GraphDbError(outcome.error)
        people = [("Ada", "engineer"), ("Grace", "researcher"), ("Linus", "maintainer"), ("Margaret", "lead"),
                  ("Alan", "analyst"), ("Katherine", "navigator")]
        for name, role in people:
            self.query("MERGE (p:Person {name: $name}) SET p.role = $role", {"name": name, "role": role})
        for name, country in (("London", "UK"), ("Helsinki", "Finland"), ("Boston", "USA")):
            self.query("MERGE (c:City {name: $name}) SET c.country = $country", {"name": name, "country": country})
        for name, status in (("Engine", "active"), ("Compiler", "done"), ("Apollo", "active")):
            self.query("MERGE (p:Project {name: $name}) SET p.status = $status", {"name": name, "status": status})
        for a, b, since in (("Ada", "Grace", 1950), ("Grace", "Margaret", 1965), ("Linus", "Alan", 1991),
                            ("Margaret", "Katherine", 1962), ("Alan", "Ada", 1940)):
            self.query("MATCH (a:Person {name: $a}), (b:Person {name: $b}) MERGE (a)-[k:Knows]->(b) SET k.since = $s",
                       {"a": a, "b": b, "s": since})
        for person, city in (("Ada", "London"), ("Alan", "London"), ("Linus", "Helsinki"), ("Margaret", "Boston"),
                             ("Grace", "Boston"), ("Katherine", "Boston")):
            self.query("MATCH (p:Person {name: $p}), (c:City {name: $c}) MERGE (p)-[:LivesIn]->(c)", {"p": person, "c": city})
        for person, project, hours in (("Ada", "Engine", 30), ("Grace", "Compiler", 40), ("Margaret", "Apollo", 50),
                                       ("Katherine", "Apollo", 20), ("Linus", "Compiler", 10)):
            self.query("MATCH (p:Person {name: $p}), (j:Project {name: $j}) MERGE (p)-[w:WorksOn]->(j) SET w.hours = $h",
                       {"p": person, "j": project, "h": hours})
# --- END [1.3] ---
