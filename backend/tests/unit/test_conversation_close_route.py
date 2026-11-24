from bank_ai.api.main import create_app


def test_conversation_close_route_registered():
    """Проверить регистрацию endpoint закрытия диалога."""

    app = create_app()

    paths = app.openapi()["paths"]

    assert "post" in paths["/conversations/{conversation_id}/close"]
