# ==============================================================================
# FILE: tests/test_db_editor.py
# OVERVIEW: Tests for the Database Editor: engine (databases, rows, schema, SQL, safety) and the module's
#           blocks working together.
# PURPOSE:
#   1. Engine: create / import / export databases, row keys (rowid and primary key), edits, schema changes,
#      scripts with errors, the read-only system database.
#   2. Module: data grid edits, record form autosave, paging follows the current row, SQL console, blocks on demand.
# CONTRACT:
#   - Requires: tests/conftest.py fixtures (qapp, database, pump), app.modules.db_editor, app.modules.registry
#   - Exposes: pytest tests
# NOTES:
#   - [2026-11-16] Created with the Database Editor module.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - shop: a test database with a products table (id, name, price) and 450 rows.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. TESTS
#    1.1 engine
#    1.2 module
# ==============================================================================

import pytest


# ==============================================================================
# [1.1] engine
# ==============================================================================
def _shop(name: str = "Shop"):
    from app.modules.db_editor.engine import ColumnDef, DbConnection, UserDatabases
    databases = UserDatabases()
    entry = databases.create(name)
    connection = DbConnection(entry)
    connection.create_table("products", [ColumnDef("id", "INTEGER", True), ColumnDef("name", "TEXT"), ColumnDef("price", "REAL")])
    for index in range(450):
        connection.insert_row("products", {"name": f"p{index}", "price": index * 1.5})
    return databases, entry, connection


def test_engine_rows_schema_and_sql(qapp, database, tmp_path):
    from app.modules.db_editor.engine import (
        ColumnDef, DbConnection, DbEditorError, coerce_value, split_statements,
    )
    databases, entry, connection = _shop()
    page = connection.fetch_rows("products", limit=10, offset=440, order_by="price", descending=True)
    assert page.total_rows == 450 and len(page.rows) == 10 and page.is_editable and page.keys[0] == {"rowid": 10}
    connection.update_cell("products", {"rowid": 1}, "price", coerce_value("9.5", "REAL"))
    assert connection.fetch_rows("products", 1).rows[0] == [1, "p0", 9.5]
    assert coerce_value("", "INTEGER") is None and coerce_value("", "TEXT") == "" and coerce_value("7", "TEXT") == "7"

    connection.create_table("tags", [ColumnDef("a", "TEXT", True), ColumnDef("b", "TEXT", True)])
    connection.execute_script("ALTER TABLE tags RENAME TO pairs; INSERT INTO pairs VALUES ('x', 'y');")
    connection.execute_script("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID; INSERT INTO kv VALUES ('a', '1');")
    kv = connection.fetch_rows("kv")
    assert kv.keys == [{"k": "a"}] and kv.is_editable                  # no rowid: the primary key finds the row
    connection.update_cell("kv", kv.keys[0], "v", "2")
    assert connection.fetch_rows("kv").rows == [["a", "2"]]

    connection.add_column("products", ColumnDef("stock", "INTEGER", default="0"))
    connection.rename_column("products", "stock", "qty")
    connection.create_index("idx_price", "products", ["price"])
    assert [column.name for column in connection.columns("products")][-1] == "qty"
    assert connection.indexes("products")[0][0] == "idx_price"

    assert split_statements("select ';'; create trigger t after insert on kv begin select 1; end; select 2") == [
        "select ';';", "create trigger t after insert on kv begin select 1; end;", "select 2"]
    results, error = connection.execute_script("select count(*) from products; select * from nowhere; select 1")
    assert results[0].rows == [(450,)] and len(results) == 1 and "nowhere" in error

    deleted = connection.delete_rows("products", [{"rowid": 1}, {"rowid": 2}])
    assert deleted == 2 and connection.row_count("products") == 448
    exported = databases.export(entry.item_id, tmp_path / "copy.sqlite3")
    assert DbConnection(databases.import_file(exported)).row_count("products") == 448
    not_a_database = tmp_path / "notes.db"
    not_a_database.write_text("hello")
    with pytest.raises(DbEditorError):
        databases.import_file(not_a_database)


def test_system_database_is_read_only(qapp, database):
    from app.modules.db_editor.engine import SYSTEM_DATABASE_ID, DbConnection, DbEditorError, UserDatabases
    databases = UserDatabases()
    system = databases.entry(SYSTEM_DATABASE_ID)
    connection = DbConnection(system)
    assert system.is_read_only and "user_preferences" in connection.tables()
    assert not connection.fetch_rows("user_preferences").is_editable
    with pytest.raises(DbEditorError):
        connection.insert_row("user_preferences", {"key": "x", "value_json": "1", "updated_at": 0})
    _results, error = connection.execute_script("DELETE FROM user_preferences")
    assert error and "readonly" in error.replace("-", "").replace(" ", "")
    with pytest.raises(DbEditorError):
        databases.delete(SYSTEM_DATABASE_ID)
# --- END [1.1] ---

# ==============================================================================
# [1.2] module
# ==============================================================================
def test_db_editor_blocks_work_together(qapp, database, pump):
    from app.modules.registry import create_module
    _databases, entry, _connection = _shop("Module shop")
    module, _context = create_module("db_editor", database)
    module.open_database(entry.item_id)
    assert module.table == "products"
    blocks = {tile.kind: tile.content_widget for tile in module.block_canvas.tiles.values()}
    assert set(blocks) == {"explorer", "data", "record", "sql"}
    data, record, sql = blocks["data"], blocks["record"], blocks["sql"]
    assert data.page_label.text() == "1–200 of 450"

    model = data.data_table_view.model()
    assert model.setData(model.index(0, 1), "first")             # edit in the grid
    assert module.connection.fetch_rows("products", 1).rows[0][1] == "first"

    module.select_row_at(260)                                    # grid pages to the row, form shows it
    assert data.page_label.text() == "201–400 of 450" and data.data_table_view.currentIndex().row() == 60
    assert record.fields["name"][0].text() == "p260"
    record.fields["name"][0].setText("edited in the form")
    record._mark_dirty("name")
    record._step(1)                                              # moving on saves the edit
    assert module.connection.fetch_rows("products", 1, 260).rows[0][1] == "edited in the form"
    assert record.record_label.text() == "Row 262 of 450"

    sql.sql_editor.setPlainText("UPDATE products SET price = 0 WHERE id <= 5; SELECT count(*) FROM products WHERE price = 0;")
    sql.run()
    assert "5 row(s) changed" in sql.sql_message_label.text() and sql.sql_results_view.model().rows == [[5]]
    sql.sql_editor.setPlainText("SELECT * FROM missing")
    sql.run()
    assert "missing" in sql.sql_message_label.text() and "failed" in module.db_status_label.text()

    module.show_block("structure")
    structure = next(tile.content_widget for tile in module.block_canvas.tiles.values() if tile.kind == "structure")
    assert structure.columns_table.rowCount() == 3
    explorer = blocks["explorer"]
    names = [explorer.db_tree.topLevelItem(index).text(0) for index in range(explorer.db_tree.topLevelItemCount())]
    assert "Module shop" in names and names[-1].startswith("DevOS system database")
    module.hide()
# --- END [1.2] ---
