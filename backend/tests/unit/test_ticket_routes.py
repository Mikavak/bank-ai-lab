from bank_ai.api.main import create_app


def test_ticket_routes_registered():
    app = create_app()

    paths = app.openapi()["paths"]

    assert "post" in paths["/conversations/{conversation_id}/tickets"]

    assert "get" in paths["/operator/tickets"]

    assert "post" in paths["/operator/tickets/{ticket_id}/claim"]

    assert "get" in paths["/operator/tickets/assigned"]

    assert "get" in paths["/operator/tickets/{ticket_id}/messages"]

    assert "post" in paths["/operator/tickets/{ticket_id}/messages"]

    assert "post" in paths["/operator/tickets/{ticket_id}/resolve"]
