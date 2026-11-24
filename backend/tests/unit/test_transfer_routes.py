from bank_ai.api.main import create_app


def test_transfer_routes_registered():
    """Проверить регистрацию всех маршрутов передачи."""

    app = create_app()

    paths = app.openapi()["paths"]

    assert "post" in paths["/operator/tickets/{ticket_id}/transfer"]

    assert "get" in paths["/operator/transfers/incoming"]

    assert "post" in paths["/operator/transfers/{transfer_id}/accept"]

    assert "post" in paths["/operator/transfers/{transfer_id}/reject"]
