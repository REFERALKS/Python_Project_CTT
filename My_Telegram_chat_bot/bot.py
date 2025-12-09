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
MAX_HISTORY = 40  # ⚡ ограничение длины памяти!

bot = telebot.TeleBot(API_TOKEN)
client = OpenAI(base_url=BASE_URL, api_key="lm-studio")

ROLES = {
    "default": "Ты полезный ассистент.",
    "coder": "Ты senior python разработчик.",
    "translator": "Ты переводчик.",
    "physicist": "Ты профессор физики.",
    "creative": "Ты креативный писатель."
}

THINKING_INSTRUCTION = "МЫСЛИ:[твои рассуждения]\nОТВЕТ:[готовый ответ]"

# ================= JSON STORAGE =================
def load_json(filename, default_data):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return default_data
    return default_data

def save_json(filename, data):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

chat_histories = load_json(HISTORY_FILE, {})
user_settings = load_json(SETTINGS_FILE, {})

# ================= SETTINGS =================
def get_settings(user_id):
    default_cfg = {"role": "default", "temperature": 0.7, "model": "local_default"}
    if user_id not in user_settings:
        user_settings[user_id] = default_cfg.copy()
    else:
        for k, v in default_cfg.items():
            if k not in user_settings[user_id]:
                user_settings[user_id][k] = v
    save_json(SETTINGS_FILE, user_settings)
    return user_settings[user_id]

def get_system_prompt(user_id):
    s = get_settings(user_id)
    return ROLES.get(s["role"], ROLES["default"]) + "\n" + THINKING_INSTRUCTION

def init_history(user_id):
    chat_histories[user_id] = [{"role": "system", "content": get_system_prompt(user_id)}]
    save_json(HISTORY_FILE, chat_histories)

def auto_trim_history(user_id):
    hist = chat_histories[user_id]
    while len(hist) > MAX_HISTORY:
        hist.pop(1)  # не удаляем system prompt

# ================= KEYBOARDS =================
def main_menu_keyboard(user_id):
    s = get_settings(user_id)
    percent = int(len(chat_histories.get(user_id, [])) / MAX_HISTORY * 100)
    bar = "▓" * (percent // 10) + "░" * (12 - (percent // 10))

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("New chat 🗑", callback_data="new_chat"),
        types.InlineKeyboardButton(f"Context {percent}% {bar}", callback_data="show_history")
    )
    markup.add(
        types.InlineKeyboardButton(f"Role 🎭 ({s['role']})", callback_data="menu_roles"),
        types.InlineKeyboardButton(f"Model 🤖 ({s['model']})", callback_data="menu_models")
    )
    markup.add(types.InlineKeyboardButton(f"Temperature 🌡 ({s['temperature']})", callback_data="menu_temp"))
    return markup

def roles_keyboard(user_id):
    current = get_settings(user_id)["role"]
    markup = types.InlineKeyboardMarkup()
    for r in ROLES.keys():
        markup.add(types.InlineKeyboardButton(f"{r} {'✔' if r == current else ''}", callback_data=f"set_role_{r}"))
    markup.add(types.InlineKeyboardButton("⬅ Back", callback_data="main_menu"))
    return markup

def models_keyboard(user_id):
    current = get_settings(user_id)["model"]
    markup = types.InlineKeyboardMarkup()
    for m in AVAILABLE_MODELS.keys():
        markup.add(types.InlineKeyboardButton(f"{m} {'✔' if m == current else ''}", callback_data=f"set_model_{m}"))
    markup.add(types.InlineKeyboardButton("⬅ Back", callback_data="main_menu"))
    return markup

def temp_keyboard(user_id):
    current = get_settings(user_id)["temperature"]
    markup = types.InlineKeyboardMarkup()
    for t in ["0.1","0.3","0.7","1.0"]:
        markup.add(types.InlineKeyboardButton(f"{t} {'✔' if float(t)==current else ''}", callback_data=f"set_temp_{t}"))
    markup.add(types.InlineKeyboardButton("⬅ Back", callback_data="main_menu"))
    return markup

# ================= START =================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    get_settings(user_id)
    bot.reply_to(message, "Панель управления:", reply_markup=main_menu_keyboard(user_id))

# ================= CALLBACKS =================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    s = get_settings(user_id)

    if call.data == "main_menu":
        bot.edit_message_text("Меню:", call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard(user_id))
    elif call.data == "new_chat":
        init_history(user_id)
        bot.send_message(call.message.chat.id, "История очищена!", reply_markup=main_menu_keyboard(user_id))
    elif call.data == "menu_models":
        bot.edit_message_text("Выберите модель:", call.message.chat.id, call.message.message_id,
                              reply_markup=models_keyboard(user_id))
    elif call.data.startswith("set_model_"):
        s["model"] = call.data.replace("set_model_", "")
        save_json(SETTINGS_FILE, user_settings)
        bot.edit_message_text("Модель установлена.", call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard(user_id))
    elif call.data == "menu_roles":
        bot.edit_message_text("Выберите роль:", call.message.chat.id, call.message.message_id,
                              reply_markup=roles_keyboard(user_id))
    elif call.data.startswith("set_role_"):
        s["role"] = call.data.replace("set_role_", "")
        save_json(SETTINGS_FILE, user_settings)
        init_history(user_id)
        bot.edit_message_text("Роль применена!", call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard(user_id))
    elif call.data == "menu_temp":
        bot.edit_message_text("Температура:", call.message.chat.id, call.message.message_id,
                              reply_markup=temp_keyboard(user_id))
    elif call.data.startswith("set_temp_"):
        s["temperature"] = float(call.data.replace("set_temp_", ""))
        save_json(SETTINGS_FILE, user_settings)
        bot.edit_message_text("Температура обновлена!", call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard(user_id))
    elif call.data == "show_history":
        hist = chat_histories.get(user_id, [])
        percent = int(len(hist)/MAX_HISTORY*100)
        bar = "▓"*(percent//10)+"░"*(12-(percent//10))
        status = "🟢 Свободно" if percent < 70 else "🟡 Нагружено" if percent < 100 else "🔴 Переполнено!"
        bot.send_message(call.message.chat.id,
                         f"📜 В памяти сообщений: {len(hist)}\n📊 Контекст: {bar} {percent}%\n⚡ Статус: {status}",
                         reply_markup=main_menu_keyboard(user_id))

# ================= MESSAGE HANDLER =================
@bot.message_handler(content_types=['text','photo'])
def handle_message(message):
    user_id = message.from_user.id
    if user_id not in chat_histories:
        init_history(user_id)

    history = chat_histories[user_id]
    s = get_settings(user_id)
    auto_trim_history(user_id)
    save_json(HISTORY_FILE, chat_histories)

    model_api_name = AVAILABLE_MODELS.get(s["model"], "local-model")
    loading = bot.reply_to(message, "⏳ Думаю...")

    try:
        # ⚡ формируем корректный content для ministral
        if message.photo:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded = bot.download_file(file_info.file_path)
            base64_image = base64.b64encode(downloaded).decode('utf-8')
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                },
                {"type": "text", "text": message.caption or "Опиши картинку"}
            ]
        else:
            user_content = [{"type": "text", "text": message.text}]

        history.append({"role":"user","content":user_content})
        save_json(HISTORY_FILE, chat_histories)

        completion = client.chat.completions.create(
            model=model_api_name,
            messages=history,
            temperature=s["temperature"]
        )

        response = completion.choices[0].message.content
        if "ОТВЕТ:" in response:
            response = response.split("ОТВЕТ:")[1]

        bot.delete_message(message.chat.id, loading.message_id)
        bot.send_message(message.chat.id, response, reply_markup=main_menu_keyboard(user_id))

        history.append({"role":"assistant","content":[{"type":"text","text":response}]})
        save_json(HISTORY_FILE, chat_histories)

    except Exception as e:
        bot.delete_message(message.chat.id, loading.message_id)
        bot.send_message(message.chat.id, f"Ошибка: {e}", reply_markup=main_menu_keyboard(user_id))

print("BOT READY ✔")
bot.polling(non_stop=True)
