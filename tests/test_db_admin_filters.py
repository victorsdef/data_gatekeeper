import importlib


def test_db_name_filter_allows_matching_database(monkeypatch):
    import utils.db_admin as db_admin

    db_admin = importlib.reload(db_admin)
    monkeypatch.setattr(db_admin, "DB_NAME_FILTERS", "CATALOGO")

    assert db_admin._is_allowed_database("db_catalogo_SS", {"mysql"}) is True
    assert db_admin._is_allowed_database("ventas", {"mysql"}) is False


def test_db_name_filter_allows_all_when_empty(monkeypatch):
    import utils.db_admin as db_admin

    db_admin = importlib.reload(db_admin)
    monkeypatch.setattr(db_admin, "DB_NAME_FILTERS", "")

    assert db_admin._is_allowed_database("ventas", {"mysql"}) is True
    assert db_admin._is_allowed_database("mysql", {"mysql"}) is False
