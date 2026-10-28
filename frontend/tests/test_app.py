from streamlit.testing.v1 import AppTest

import client

PENDING = {
    "thread_id": "t1",
    "status": "pending_confirmation",
    "summary": "Vas a transferir $50.000,00 a Juan Pérez. ¿Confirmás?",
}
DONE = {"thread_id": "t1", "status": "completed", "reply": "Transferencia realizada."}


def logged_in_app(monkeypatch, decisions: list[bool]) -> AppTest:
    monkeypatch.setattr(client.Backend, "login", lambda self, u, p: "tok")
    monkeypatch.setattr(client.Backend, "chat", lambda self, token, m, t: PENDING)

    def confirm(self, token, thread_id, approve):
        decisions.append(approve)
        return DONE

    monkeypatch.setattr(client.Backend, "confirm", confirm)

    app = AppTest.from_file("../app.py").run()
    app.text_input[0].set_value("alice")
    app.text_input[1].set_value("pw")
    app.button[0].click().run()
    return app


def test_login_chat_and_confirm_flow(monkeypatch) -> None:
    decisions: list[bool] = []
    app = logged_in_app(monkeypatch, decisions)
    assert app.session_state["token"] == "tok"

    app.chat_input[0].set_value("Mandale 50000 a Juan").run()
    assert app.warning[0].value == PENDING["summary"]
    assert app.chat_input[0].disabled
    assert {b.label for b in app.button} == {"Cerrar sesión", "Confirmar", "Cancelar"}

    next(b for b in app.button if b.label == "Confirmar").click().run()
    assert decisions == [True]
    assert not app.warning
    assert app.chat_message[-1].markdown[0].value == "Transferencia realizada."


def test_cancel_sends_a_negative_decision(monkeypatch) -> None:
    decisions: list[bool] = []
    app = logged_in_app(monkeypatch, decisions)
    app.chat_input[0].set_value("Mandale 50000 a Juan").run()
    next(b for b in app.button if b.label == "Cancelar").click().run()
    assert decisions == [False]


def test_logout_clears_the_session(monkeypatch) -> None:
    app = logged_in_app(monkeypatch, [])
    app.chat_input[0].set_value("hola").run()
    next(b for b in app.button if b.label == "Cerrar sesión").click().run()
    assert app.session_state["token"] is None
    assert app.session_state["history"] == []
