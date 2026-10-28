import os

import streamlit as st

from client import Backend, BackendError, SessionExpired

backend = Backend(
    os.environ.get("IDP_URL", "http://localhost:8001"),
    os.environ.get("AGENT_URL", "http://localhost:8003"),
)

st.set_page_config(page_title="Billetera", page_icon="💬")
state = st.session_state
state.setdefault("token", None)
state.setdefault("thread_id", None)
state.setdefault("history", [])
state.setdefault("pending_summary", None)


def reset_session() -> None:
    for key in ("token", "thread_id", "pending_summary"):
        state[key] = None
    state["history"] = []


def apply_response(body: dict) -> None:
    state["thread_id"] = body["thread_id"]
    if body["status"] == "pending_confirmation":
        state["pending_summary"] = body["summary"]
    else:
        state["pending_summary"] = None
        state["history"].append(("assistant", body["reply"]))


def call(action, *args) -> None:
    try:
        apply_response(action(state["token"], *args))
    except SessionExpired:
        reset_session()
        st.session_state["notice"] = "Tu sesión expiró. Ingresá de nuevo."
    except BackendError as exc:
        state["history"].append(("assistant", f"⚠️ {exc.message}"))


if state["token"] is None:
    st.title("Billetera")
    if notice := state.pop("notice", None):
        st.warning(notice)
    with st.form("login"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        if st.form_submit_button("Ingresar"):
            try:
                state["token"] = backend.login(username, password)
                st.rerun()
            except BackendError as exc:
                st.error(exc.message)
    st.stop()

with st.sidebar:
    if st.button("Cerrar sesión"):
        reset_session()
        st.rerun()

st.title("Asistente de tu billetera")
for role, text in state["history"]:
    st.chat_message(role).write(text)

if state["pending_summary"]:
    st.warning(state["pending_summary"])
    confirm_col, cancel_col = st.columns(2)
    if confirm_col.button("Confirmar", type="primary", use_container_width=True):
        call(backend.confirm, state["thread_id"], True)
        st.rerun()
    if cancel_col.button("Cancelar", use_container_width=True):
        call(backend.confirm, state["thread_id"], False)
        st.rerun()

if prompt := st.chat_input("Escribí tu consulta", disabled=bool(state["pending_summary"])):
    state["history"].append(("user", prompt))
    with st.spinner("Pensando..."):
        call(backend.chat, prompt, state["thread_id"])
    st.rerun()
