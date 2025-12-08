import telebot
from telebot import types
from openai import OpenAI
import json
import os
import base64

# --- CONFIG ---
API_TOKEN = "8057312342:AAEpPXaXZdgWyfTOK3IAeTIChDNZy6pUKP0"
MY_USER_ID = 5178568186
HISTORY_FILE = 'history.json'
SETTINGS_FILE = 'settings.json'

AVAILABLE_MODELS = {
    "ministral": "mistralai/ministral-3-14b-reasoning",
    "qwen_vl": "qwen/qwen3-vl-30b",
    "local_default": "local-model"
}

BASE_URL = "http://localhost:1234/v1"

bot = telebot.TeleBot(API_TOKEN)
client = OpenAI(base_url=BASE_URL, api_key="lm-studio")

ROLES = {
    "default": "Ты полезный ассистент.",
    "coder": "Ты senior python разработчик.",
    "translator": "Ты переводчик.",
    "physicist": "Ты профессор физики.",
    "creative": "Ты креативный писатель."
}

THINKING_INSTRUCTION = (
    "МЫСЛИ:[твои рассуждения]\n"
    "ОТВЕТ:[готовый ответ]"
)

def load_json(filename, default_data):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k) if k.isdigit() else k: v for k, v in data.items()}
        except:
            return default_data
    return default_data

def save_json(filename, data):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

chat_histories = load_json(HISTORY_FILE, {})
user_settings = load_json(SETTINGS_FILE, {})

def get_settings(user_id):
    default_cfg = {
        "role": "default",
        "temperature": 0.7,
        "model": "local_default"
    }

    if user_id not in user_settings:
        user_settings[user_id] = default_cfg.copy()
        save_json(SETTINGS_FILE, user_settings)
    else:
        # добавляем недостающие ключи без перезаписи существующих
        for key, val in default_cfg.items():
            if key not in user_settings[user_id]:
                user_settings[user_id][key] = val
        save_json(SETTINGS_FILE, user_settings)

    return user_settings[user_id]

def get_system_prompt(user_id):
    settings = get_settings(user_id)
    role_text = ROLES.get(settings["role"], ROLES["default"])
    return role_text + "\n" + THINKING_INSTRUCTION

def init_history(user_id):
    sys_prompt = get_system_prompt(user_id)
    chat_histories[user_id] = [{"role": "system", "content": sys_prompt}]
    save_json(HISTORY_FILE, chat_histories)

def main_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("New chat", callback_data="new_chat"),
        types.InlineKeyboardButton("History", callback_data="show_history"),
        types.InlineKeyboardButton("Role", callback_data="menu_roles"),
        types.InlineKeyboardButton("Model", callback_data="menu_models"),
        types.InlineKeyboardButton("Temperature", callback_data="menu_temp")
    )
    return markup

def roles_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(k.capitalize(), callback_data=f"set_role_{k}")
        for k in ROLES.keys()
    ]
    markup.add(*buttons)
    markup.add(types.InlineKeyboardButton("Back", callback_data="main_menu"))
    return markup

def models_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    for friendly_name in AVAILABLE_MODELS.keys():
        markup.add(types.InlineKeyboardButton(friendly_name, callback_data=f"set_model_{friendly_name}"))
    markup.add(types.InlineKeyboardButton("Back", callback_data="main_menu"))
    return markup

def temp_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("0.1", callback_data="set_temp_0.1"),
        types.InlineKeyboardButton("0.3", callback_data="set_temp_0.3"),
        types.InlineKeyboardButton("0.7", callback_data="set_temp_0.7"),
        types.InlineKeyboardButton("1.0", callback_data="set_temp_1.0"),
        types.InlineKeyboardButton("Back", callback_data="main_menu")
    )
    return markup

# --- COMMANDS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if message.from_user.id != MY_USER_ID: return
    current_model = get_settings(MY_USER_ID)["model"]
    bot.reply_to(message, f"Панель управления.\nТекущая модель: {current_model}",
                 reply_markup=main_menu_keyboard())

# --- CALLBACKS ---

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    if user_id != MY_USER_ID: return

    settings = get_settings(user_id)

    if call.data == "main_menu":
        bot.edit_message_text(f"Панель управления. Модель: {settings['model']}",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard())

    elif call.data == "new_chat":
        init_history(user_id)
        bot.send_message(call.message.chat.id, "История очищена!",
                         reply_markup=main_menu_keyboard())

    elif call.data == "menu_models":
        bot.edit_message_text("Выберите модель:",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=models_keyboard())

    elif call.data.startswith("set_model_"):
        model_key = call.data.replace("set_model_", "")
        settings["model"] = model_key
        save_json(SETTINGS_FILE, user_settings)
        bot.answer_callback_query(call.id, "Модель обновлена")
        bot.edit_message_text(f"Выбрана модель: {model_key}",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard())

    elif call.data == "menu_roles":
        bot.edit_message_text("Выберите роль:",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=roles_keyboard())

    elif call.data.startswith("set_role_"):
        new_role = call.data.replace("set_role_", "")
        settings["role"] = new_role
        save_json(SETTINGS_FILE, user_settings)
        init_history(user_id)
        bot.answer_callback_query(call.id, "Роль установлена")
        bot.edit_message_text(f"Роль: {new_role}",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard())

    elif call.data == "menu_temp":
        bot.edit_message_text(f"Температура: {settings['temperature']}",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=temp_keyboard())

    elif call.data.startswith("set_temp_"):
        new_temp = float(call.data.replace("set_temp_", ""))
        settings["temperature"] = new_temp
        save_json(SETTINGS_FILE, user_settings)
        bot.answer_callback_query(call.id, "Температура обновлена")
        bot.edit_message_text(f"Температура: {new_temp}",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard())

    elif call.data == "show_history":
        history = chat_histories.get(user_id, [])
        bot.send_message(call.message.chat.id, f"Сообщений в памяти: {len(history)}")

# --- MESSAGE HANDLER ---

@bot.message_handler(content_types=['text', 'photo'])
def handle_message(message):
    if message.from_user.id != MY_USER_ID: return

    user_id = message.from_user.id
    if user_id not in chat_histories:
        init_history(user_id)

    history = chat_histories[user_id]
    settings = get_settings(user_id)

    model_api_name = AVAILABLE_MODELS.get(settings["model"], "local-model")

    loading = bot.reply_to(message, f"Модель: {settings['model']} — Думает...")

    try:
        if message.photo:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded = bot.download_file(file_info.file_path)
            base64_image = base64.b64encode(downloaded).decode('utf-8')
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                {"type": "text", "text": message.caption or "Опиши изображение"}
            ]
        else:
            user_content = message.text

        history.append({"role": "user", "content": user_content})
        save_json(HISTORY_FILE, chat_histories)

        completion = client.chat.completions.create(
            model=model_api_name,
            messages=history,
            temperature=settings["temperature"]
        )

        response = completion.choices[0].message.content

        if "ОТВЕТ:" in response:
            response = response.split("ОТВЕТ:")[1]

        bot.delete_message(message.chat.id, loading.message_id)
        bot.send_message(message.chat.id, response)

        history.append({"role": "assistant", "content": response})
        save_json(HISTORY_FILE, chat_histories)

    except Exception as e:
        bot.edit_message_text(f"Ошибка: {e}", message.chat.id, loading.message_id)

print("BOT READY ✔")
bot.polling(non_stop=True)
