import streamlit as st
import ollama
from pydantic import BaseModel, Field
from typing import List, Dict, Any
import json

# --- 1. Pydantic Schemas ---

class InventoryItem(BaseModel):
    item_name: str = Field(description="Название предмета.")
    quantity: int = Field(description="Количество этого предмета (целое число).")

class CharacterClass(BaseModel):
    name: str = Field(description="Название класса (напр., 'Воин', 'Маг').")
    level: int = Field(description="Уровень персонажа, целое число от 1 до 20.")

class CharacterProfile(BaseModel):
    name: str = Field(description="Имя персонажа.")
    race: str = Field(description="Раса персонажа.")
    strength: int = Field(description="Сила персонажа, целое число от 1 до 18.")
    agility: int = Field(description="Ловкость персонажа, целое число от 1 до 18.")
    intelligence: int = Field(description="Интеллект персонажа, целое число от 1 до 18.")
    char_class: CharacterClass = Field(alias="class", description="Информация о классе.")
    inventory: List[InventoryItem] = Field(description="Список предметов в инвентаре.")

# --- 2. LLM Setup ---

MODEL_NAME = 'llama3'

SYSTEM_PROMPT = """
Ты — генератор фэнтези-персонажей. Верни **только JSON**, строго по этой структуре:

{
  "name": "строка",
  "race": "строка",
  "strength": целое число 1-18,
  "agility": целое число 1-18,
  "intelligence": целое число 1-18,
  "class": {
    "name": "строка",
    "level": 1-20
  },
  "inventory": [
    {"item_name": "строка", "quantity": целое число}
  ]
}
"""

# --- 3. Function to call LLM ---

def generate_structured_data(model: str, system_p: str, user_p: str, schema: BaseModel) -> Dict[str, Any]:
    """
    Запрос к LLM без format.schema (устойчиво), валидация через Pydantic после получения.
    """
    try:
        with st.spinner(f"Запрос к модели {model}..."):
            response = ollama.chat(
                model=model,
                messages=[
                    {'role': 'system', 'content': system_p},
                    {'role': 'user', 'content': user_p}
                ],
                options={'temperature': 0.1}
            )

        json_string = response['message']['content']
        # Валидация Pydantic
        validated_data = schema.model_validate_json(json_string)
        return validated_data.model_dump(by_alias=True)

    except Exception as e:
        st.error(f"Ошибка LLM/Парсинга: {e}")
        st.caption("Fallback активирован — выдаем базовый персонаж 🚀")

        # fallback с alias
        fallback = CharacterProfile(
            name="Ошибка генерации",
            race="Неизвестна",
            strength=10,
            agility=10,
            intelligence=10,
            **{"class": CharacterClass(name="Новичок", level=1)},
            inventory=[]
        )
        return fallback.model_dump(by_alias=True)

# --- 4. Streamlit UI ---

def main():
    st.set_page_config(page_title="LLM JSON Generator", layout="wide")
    st.title("🧙‍♂️ Генератор Персонажа (Ollama + Streamlit)")
    st.caption(f"Используемая модель: **{MODEL_NAME}**")
    st.divider()

    # Состояние сессии
    if 'character_data' not in st.session_state:
        st.session_state.character_data = {}
        st.session_state.user_input = "Сгенерируй эльфа-мага 12 уровня с посохом."

    st.session_state.user_input = st.text_input(
        "📝 Промпт для генерации персонажа:",
        st.session_state.user_input
    )

    if st.button("✨ Сгенерировать персонажа", type="primary"):
        st.session_state.character_data = generate_structured_data(
            MODEL_NAME, SYSTEM_PROMPT, st.session_state.user_input, CharacterProfile
        )

    # Отображение данных
    if st.session_state.character_data:
        data = st.session_state.character_data

        st.header(f"{data.get('name')} — {data.get('race')}")

        # Атрибуты
        st.subheader("Атрибуты")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.number_input("Сила", 1, 18, value=data.get('strength'), key="str_input")
        with col2:
            st.number_input("Ловкость", 1, 18, value=data.get('agility'), key="agi_input")
        with col3:
            st.number_input("Интеллект", 1, 18, value=data.get('intelligence'), key="int_input")

        # Класс
        st.subheader("Класс")
        c1, c2 = st.columns(2)
        with c1:
            st.text_input("Класс", value=data.get("class", {}).get("name"), key="class_name_input")
        with c2:
            st.number_input("Уровень", 1, 20, value=data.get("class", {}).get("level"), key="level_input")

        # Инвентарь
        st.subheader("🎒 Инвентарь")
        if data.get("inventory"):
            st.data_editor(data["inventory"], num_rows="dynamic", key="inventory_editor", use_container_width=True)

        # JSON
        st.divider()
        st.subheader("Сырой JSON")
        st.json(data)

        # Обратная связь
        current_state_json = json.dumps({
            "name": data.get('name'),
            "strength": st.session_state.str_input,
            "level": st.session_state.level_input,
            "inventory": st.session_state.inventory_editor if 'inventory_editor' in st.session_state else data.get('inventory')
        }, indent=2)

        st.subheader("♻️ JSON для LLM обратного запроса")
        st.code(current_state_json, language="json")

if __name__ == '__main__':
    main()
