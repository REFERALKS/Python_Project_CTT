from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from db import run_sql_query, tool_result_to_json
from llm import LLMConfig, chat_once, load_config_from_env, make_client, extract_text
from prompts import SYSTEM_PROMPT


def tool_spec_run_sql_query() -> dict[str, Any]:
    # OpenAI tools schema
    return {
        "type": "function",
        "function": {
            "name": "run_sql_query",
            "description": (
                "Execute a READ-ONLY SQL query (SELECT/WITH) against the shop SQLite database "
                "and return rows/columns. Use this to answer questions about products, prices, quantities, categories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL query (only SELECT/WITH). Example: SELECT * FROM products LIMIT 5",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


def get_default_db_path() -> str:
    # Default db location: ./data/shop.db
    return str(Path(__file__).with_name("data") / "shop.db")


def ensure_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []  # chat history for the model
    if "ui_messages" not in st.session_state:
        st.session_state.ui_messages = []  # chat history for display (same content, but we can include extras)
    if "last_sql" not in st.session_state:
        st.session_state.last_sql = None


def add_ui_message(role: str, content: str) -> None:
    st.session_state.ui_messages.append({"role": role, "content": content})


def add_model_message(role: str, content: str, **kwargs: Any) -> None:
    msg = {"role": role, "content": content}
    msg.update(kwargs)
    st.session_state.messages.append(msg)


def render_chat() -> None:
    for m in st.session_state.ui_messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])


def main() -> None:
    st.set_page_config(page_title="AI DB Assistant (Tool Calls)", layout="wide")
    ensure_session_state()

    st.title("AI-ассистент с доступом к SQLite (Tool Calls)")

    cfg_env = load_config_from_env()

    with st.sidebar:
        st.header("Настройки")
        base_url = st.text_input("LM Studio base_url", value=cfg_env.base_url)
        api_key = st.text_input("API key (любое для LM Studio)", value=cfg_env.api_key, type="password")
        model = st.text_input("Model", value=cfg_env.model)

        db_path = st.text_input("SQLite DB path", value=os.getenv("DB_PATH", get_default_db_path()))

        st.divider()
        st.caption("Подсказка: сначала запустите `python init_db.py`, чтобы создать data/shop.db")
        if st.session_state.last_sql:
            st.caption("Последний SQL:")
            st.code(st.session_state.last_sql, language="sql")

        if st.button("Очистить чат", type="secondary"):
            st.session_state.messages = []
            st.session_state.ui_messages = []
            st.session_state.last_sql = None
            st.rerun()

    # Quick demo button (slide 7 style)
    col1, col2 = st.columns([1, 2], gap="large")
    with col1:
        st.subheader("Демо-кнопка (без LLM)")
        if st.button("Показать дорогие товары (> 1000.00)", type="primary"):
            q = """
            SELECT p.name, c.name AS category, p.brand, p.color, p.quantity, (p.price_cents/100.0) AS price
            FROM products p
            JOIN categories c ON c.id = p.category_id
            WHERE p.price_cents > 100000
            ORDER BY p.price_cents DESC
            """
            res = run_sql_query(db_path=db_path, query=q)
            if not res["ok"]:
                st.error(res["error"])
            else:
                df = pd.DataFrame(res["rows"], columns=res["columns"])
                st.dataframe(df, use_container_width=True)

    with col2:
        st.subheader("Чат с tool calls (LLM + SQLite)")

        render_chat()

        user_text = st.chat_input("Спроси про товары, цены, остатки, категории…")
        if not user_text:
            return

        # Display user message
        add_ui_message("user", user_text)

        # Add to model messages
        if not st.session_state.messages:
            add_model_message("system", SYSTEM_PROMPT)
        add_model_message("user", user_text)

        cfg = LLMConfig(base_url=base_url, api_key=api_key, model=model)
        client = make_client(cfg)
        tools = [tool_spec_run_sql_query()]

        # Tool loop: allow a few iterations to avoid infinite loops
        max_tool_rounds = 3
        for _ in range(max_tool_rounds):
            resp = chat_once(client=client, cfg=cfg, messages=st.session_state.messages, tools=tools)
            msg = resp.choices[0].message

            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                # Record assistant message with tool calls (content may be empty)
                add_model_message("assistant", extract_text(msg), tool_calls=[tc.model_dump() for tc in tool_calls])

                # Execute each tool call
                for tc in tool_calls:
                    fn = tc.function.name
                    if fn != "run_sql_query":
                        tool_out = {"ok": False, "error": f"Unknown tool: {fn}"}
                        add_model_message(
                            role="tool",
                            content=json.dumps(tool_out, ensure_ascii=False),
                            tool_call_id=tc.id,
                            name=fn,
                        )
                        continue

                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError as e:
                        tool_out = {"ok": False, "error": f"Invalid JSON args: {e}"}
                        add_model_message(
                            role="tool",
                            content=json.dumps(tool_out, ensure_ascii=False),
                            tool_call_id=tc.id,
                            name=fn,
                        )
                        continue

                    query = str(args.get("query", "")).strip()
                    st.session_state.last_sql = query

                    result = run_sql_query(db_path=db_path, query=query)
                    tool_json = tool_result_to_json(result)

                    # Show tool result to user (compact)
                    with st.chat_message("assistant"):
                        if result["ok"]:
                            st.markdown("Я проверил базу данных.")
                            df = pd.DataFrame(result["rows"], columns=result["columns"])
                            st.dataframe(df, use_container_width=True)
                        else:
                            st.markdown("Не смог выполнить запрос к базе данных.")
                            st.code(result["error"])

                    # Send tool output back to model
                    add_model_message(
                        role="tool",
                        content=tool_json,
                        tool_call_id=tc.id,
                        name=fn,
                    )

                # Continue loop: model should now answer using tool output
                continue

            # No tool call -> final assistant answer
            final_text = extract_text(msg).strip() or "(пустой ответ модели)"
            add_model_message("assistant", final_text)
            add_ui_message("assistant", final_text)
            st.rerun()

        # If we reached max_tool_rounds without a final answer
        add_ui_message("assistant", "Я получил данные из БД, но не смог сформировать финальный ответ за отведённое число шагов. Попробуй переформулировать вопрос короче.")
        st.rerun()


if __name__ == "__main__":
    main()