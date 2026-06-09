from storage.file_handler import detect_file_delimiter, read_uploaded_file


def test_read_uploaded_csv_detects_semicolon_delimiter():
    data = b"codigo;estado\n1;A\n2;I\n"

    df, error = read_uploaded_file(data, "catalogo.csv")

    assert error is None
    assert list(df.columns) == ["codigo", "estado"]
    assert df.shape == (2, 2)


def test_read_uploaded_file_rejects_unsupported_extension():
    df, error = read_uploaded_file(b"abc", "catalogo.pdf")

    assert df is None
    assert "no soportado" in error


def test_detect_file_delimiter_returns_pipe():
    assert detect_file_delimiter(b"a|b\n1|2\n") == "|"


def test_read_uploaded_csv_falls_back_to_latin1():
    data = "codigo;descripcion\n1;Español\n".encode("cp1252")

    df, error = read_uploaded_file(data, "catalogo.csv", encoding="utf-8")

    assert error is None
    assert df.iloc[0]["descripcion"] == "Español"


def test_read_uploaded_csv_rejects_duplicate_headers():
    data = b"codigo;codigo;estado\n1;2;A\n"

    df, error = read_uploaded_file(data, "catalogo.csv", delimiter=";")

    assert df is None
    assert "columnas repetidas" in error


def test_read_uploaded_csv_rejects_data_row_used_as_duplicate_header():
    data = b"40,40,PEDIDOSYA,PEDIDOSYA,NA,NA\n9,9,PEDIDOSYA,PEDIDOSYA,NA,NA\n"

    df, error = read_uploaded_file(data, "catalogo.csv", delimiter=",")

    assert df is None
    assert "cabecera" in error
