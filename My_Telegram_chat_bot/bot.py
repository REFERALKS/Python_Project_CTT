import telebot
from telebot import types
from openai import OpenAI
import json
import os
import base64

# --- CONFIG ---
API_TOKEN = "8057312342:AAEpPXaXZdgWyfTOK3IAeTIChDNZy6pUKP0"
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

# ==============================================
# JSON LOAD/SAVE
# ==============================================
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

# ==============================================
# SETTINGS
# ==============================================
def get_settings(user_id):
    default_cfg = {
        "role": "default",
        "temperature": 0.7,
        "model": "local_default"
    }

    if user_id not in user_settings:
        user_settings[user_id] = default_cfg.copy()
    else:
        for key, value in default_cfg.items():
            if key not in user_settings[user_id]:
                user_settings[user_id][key] = value

    save_json(SETTINGS_FILE, user_settings)
    return user_settings[user_id]

def get_system_prompt(user_id):
    settings = get_settings(user_id)
    role_text = ROLES.get(settings["role"], ROLES["default"])
    return role_text + "\n" + THINKING_INSTRUCTION

def init_history(user_id):
    chat_histories[user_id] = [{"role": "system", "content": get_system_prompt(user_id)}]
    save_json(HISTORY_FILE, chat_histories)

# ==============================================
# KEYBOARDS
# ==============================================
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
    markup = types.InlineKeyboardMarkup()
    for role in ROLES.keys():
        markup.add(types.InlineKeyboardButton(role.capitalize(), callback_data=f"set_role_{role}"))
    markup.add(types.InlineKeyboardButton("Back", callback_data="main_menu"))
    return markup

def models_keyboard():
    markup = types.InlineKeyboardMarkup()
    for m in AVAILABLE_MODELS.keys():
        markup.add(types.InlineKeyboardButton(m, callback_data=f"set_model_{m}"))
    markup.add(types.InlineKeyboardButton("Back", callback_data="main_menu"))
    return markup

def temp_keyboard():
    buttons = ["0.1", "0.3", "0.7", "1.0"]
    markup = types.InlineKeyboardMarkup()
    for t in buttons:
        markup.add(types.InlineKeyboardButton(t, callback_data=f"set_temp_{t}"))
    markup.add(types.InlineKeyboardButton("Back", callback_data="main_menu"))
    return markup

# ==============================================
# START COMMAND
# ==============================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    get_settings(user_id)
    bot.reply_to(message, f"Панель управления.\n", reply_markup=main_menu_keyboard())

# ==============================================
# CALLBACKS
# ==============================================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    settings = get_settings(user_id)

    if call.data == "main_menu":
        bot.edit_message_text("Меню:", call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard())

    elif call.data == "new_chat":
        init_history(user_id)
        bot.send_message(call.message.chat.id, "История очищена!", reply_markup=main_menu_keyboard())

    elif call.data == "menu_models":
        bot.edit_message_text("Выберите модель:", call.message.chat.id, call.message.message_id,
                              reply_markup=models_keyboard())

    elif call.data.startswith("set_model_"):
        settings["model"] = call.data.replace("set_model_", "")
        save_json(SETTINGS_FILE, user_settings)
        bot.edit_message_text("Модель установлена.", call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard())

    elif call.data == "menu_roles":
        bot.edit_message_text("Выберите роль:", call.message.chat.id, call.message.message_id,
                              reply_markup=roles_keyboard())

    elif call.data.startswith("set_role_"):
        settings["role"] = call.data.replace("set_role_", "")
        save_json(SETTINGS_FILE, user_settings)
        init_history(user_id)
        bot.edit_message_text("Роль изменена!", call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard())

    elif call.data == "menu_temp":
        bot.edit_message_text(f"Температура: {settings['temperature']}",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=temp_keyboard())

    elif call.data.startswith("set_temp_"):
        settings["temperature"] = float(call.data.replace("set_temp_", ""))
        save_json(SETTINGS_FILE, user_settings)
        bot.edit_message_text("Температура обновлена.", call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard())

    elif call.data == "show_history":
        hist = chat_histories.get(user_id, [])
        bot.send_message(call.message.chat.id, f"Сообщений в памяти: {len(hist)}", reply_markup=main_menu_keyboard())

# ==============================================
# MESSAGE HANDLER
# ==============================================
@bot.message_handler(content_types=['text', 'photo'])
def handle_message(message):
    user_id = message.from_user.id
    if user_id not in chat_histories:
        init_history(user_id)

    history = chat_histories[user_id]
    settings = get_settings(user_id)
    model_api_name = AVAILABLE_MODELS.get(settings["model"], "local-model")

    loading = bot.reply_to(message, "⏳ Думаю...")

    try:
        if message.photo:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded = bot.download_file(file_info.file_path)
            base64_image = base64.b64encode(downloaded).decode('utf-8')
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                {"type": "text", "text": message.caption or "Опиши картинку"}
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

        MAX_LENGTH = 4000
        if len(response) > MAX_LENGTH:
            chunks = [response[i:i + MAX_LENGTH] for i in range(0, len(response), MAX_LENGTH)]
            for chunk in chunks[:-1]:
                bot.send_message(message.chat.id, chunk)
            bot.send_message(message.chat.id, chunks[-1], reply_markup=main_menu_keyboard())
        else:
            bot.send_message(message.chat.id, response, reply_markup=main_menu_keyboard())

        history.append({"role": "assistant", "content": response})
        save_json(HISTORY_FILE, chat_histories)

    except Exception as e:
        try:
            bot.edit_message_text(f"Ошибка: {e}", message.chat.id, loading.message_id)
        except:
            bot.send_message(message.chat.id, f"Ошибка: {e}", reply_markup=main_menu_keyboard())

print("BOT READY ✔")
bot.polling(non_stop=True)
