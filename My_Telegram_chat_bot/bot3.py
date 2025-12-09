import telebot
from telebot import types
from openai import OpenAI
import json
import os
import base64
from tiktoken import get_encoding

# --- CONFIG ---
API_TOKEN = "8057312342:AAEpPXaXZdgWyfTOK3IAeTIChDNZy6pUKP0"
HISTORY_FILE = 'history.json'
SETTINGS_FILE = 'settings.json'

TOKEN_LIMIT = 16834
tokenizer = get_encoding("cl100k_base")

AVAILABLE_MODELS = {
    "ministral": "mistralai/ministral-3-14b-reasoning",
    "qwen_vl": "qwen/qwen3-vl-30b",
    "local_default": "local-model"
}

BASE_URL = "http://localhost:1234/v1"
MAX_HISTORY = 40

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

# -------- JSON --------
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

# -------- TOKEN COUNTER --------
def count_tokens(messages):
    total = 0
    for m in messages:
        if isinstance(m.get("content"), list):
            for block in m["content"]:
                if block.get("type") == "text" and "text" in block:
                    total += len(tokenizer.encode(block["text"]))
        elif isinstance(m.get("content"), str):
            total += len(tokenizer.encode(m["content"]))
    return total

def get_token_status(user_id):
    used = count_tokens(chat_histories[user_id])
    left = TOKEN_LIMIT - used
    return used, left

# -------- SETTINGS --------
def get_settings(user_id):
    default_cfg = {"role": "default", "temperature": 0.7, "model": "local_default"}
    if user_id not in user_settings:
        user_settings[user_id] = default_cfg.copy()
    save_json(SETTINGS_FILE, user_settings)
    return user_settings[user_id]

def get_system_prompt(user_id):
    s = get_settings(user_id)
    return ROLES.get(s["role"], ROLES["default"]) + "\n" + THINKING_INSTRUCTION

def init_history(user_id):
    chat_histories[user_id] = [{"role": "system", "content": get_system_prompt(user_id)}]
    save_json(HISTORY_FILE, chat_histories)

# -------- COMPRESSION --------
def compress_summary(msgs):
    parts = []
    for msg in msgs:
        if isinstance(msg.get("content"), list):
            text_blocks = [b["text"] for b in msg["content"] if b.get("type") == "text" and "text" in b]
            if text_blocks:
                joined_text = " ".join(text_blocks)
                if msg["role"] == "user":
                    parts.append("U: " + joined_text)
                elif msg["role"] == "assistant":
                    parts.append("A: " + joined_text)
    return " | ".join(parts)

def compression_engine(user_id):
    history = chat_histories[user_id]

    # Stage 1: summary
    if len(history) > 12 and "summary" not in history[1].get("content", [{}])[0].get("text", ""):
        summary = compress_summary(history[1:8])
        chat_histories[user_id] = [history[0],
                                   {"role": "system", "content":[{"type":"text","text": f"summary: {summary}"}]}
                                   ] + history[8:]

    # Stage 2: ultra summary
    history = chat_histories[user_id]
    if len(history) > 18 and "ultra" not in history[1].get("content", [{}])[0].get("text", ""):
        old_sum = history[1]["content"][0]["text"]
        ultra = f"ultra: {old_sum[:180]}..."
        chat_histories[user_id] = [history[0],
                                   {"role":"system","content":[{"type":"text","text": ultra}]}
                                   ] + history[10:]

    # Stage 3: hard minimal
    history = chat_histories[user_id]
    if len(history) > 28:
        chat_histories[user_id] = [history[0], history[1]] + history[-6:]

    save_json(HISTORY_FILE, chat_histories)

# -------- UTIL --------
def safe_delete(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

def send_long_message(chat_id, text, reply_markup=None):
    MAX_LEN = 4000
    chunks = [text[i:i+MAX_LEN] for i in range(0, len(text), MAX_LEN)]
    for i, chunk in enumerate(chunks):
        # кнопки только на последнем сообщении
        bot.send_message(chat_id, chunk, reply_markup=reply_markup if i == len(chunks)-1 else None)


# -------- KEYBOARDS --------
def main_menu_keyboard(user_id):
    used, left = get_token_status(user_id)
    s = get_settings(user_id)
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton(f"Tokens {used}/{TOKEN_LIMIT}", callback_data="show_history"))
    markup.add(types.InlineKeyboardButton("New chat 🗑", callback_data="new_chat"))
    markup.add(types.InlineKeyboardButton(f"Role ({s['role']})", callback_data="menu_roles"))
    markup.add(types.InlineKeyboardButton(f"Model ({s['model']})", callback_data="menu_models"))
    markup.add(types.InlineKeyboardButton(f"Temp ({s['temperature']})", callback_data="menu_temp"))
    return markup

def roles_keyboard(user_id):
    current = get_settings(user_id)["role"]
    markup = types.InlineKeyboardMarkup()
    for r in ROLES.keys():
        markup.add(types.InlineKeyboardButton(f"{r} {'✔' if r==current else ''}", callback_data=f"set_role_{r}"))
    markup.add(types.InlineKeyboardButton("⬅ Back", callback_data="main_menu"))
    return markup

def models_keyboard(user_id):
    current = get_settings(user_id)["model"]
    markup = types.InlineKeyboardMarkup()
    for m in AVAILABLE_MODELS.keys():
        markup.add(types.InlineKeyboardButton(f"{m} {'✔' if m==current else ''}", callback_data=f"set_model_{m}"))
    markup.add(types.InlineKeyboardButton("⬅ Back", callback_data="main_menu"))
    return markup

def temp_keyboard(user_id):
    current = get_settings(user_id)["temperature"]
    markup = types.InlineKeyboardMarkup()
    for t in ["0.1","0.3","0.7","1.0"]:
        markup.add(types.InlineKeyboardButton(f"{t} {'✔' if float(t)==current else ''}", callback_data=f"set_temp_{t}"))
    markup.add(types.InlineKeyboardButton("⬅ Back", callback_data="main_menu"))
    return markup

# -------- START --------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    init_history(message.from_user.id)
    bot.reply_to(message, "Панель управления:", reply_markup=main_menu_keyboard(message.from_user.id))

# -------- CALLBACK HANDLER --------
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

    elif call.data.startswith("set_model_"):
        s["model"] = call.data.replace("set_model_", "")
        save_json(SETTINGS_FILE, user_settings)
        bot.edit_message_text("Модель установлена.", call.message.chat.id, call.message.message_id,
                              reply_markup=main_menu_keyboard(user_id))

    elif call.data == "menu_models":
        bot.edit_message_text("Выберите модель:", call.message.chat.id, call.message.message_id,
                              reply_markup=models_keyboard(user_id))

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
        used, left = get_token_status(user_id)
        bot.send_message(call.message.chat.id,
                         f"📜 Tokens: {used}/{TOKEN_LIMIT}\n✍ Оставшиеся: {left}",
                         reply_markup=main_menu_keyboard(user_id))

# -------- MAIN MESSAGE --------
@bot.message_handler(content_types=['text','photo'])
def handle_message(message):
    user_id = message.from_user.id
    if user_id not in chat_histories:
        init_history(user_id)

    s = get_settings(user_id)
    compression_engine(user_id)

    model_api = AVAILABLE_MODELS.get(s["model"], "local-model")
    loading = bot.reply_to(message, "⏳ Думаю...")

    try:
        if message.photo:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded = bot.download_file(file_info.file_path)
            b64 = base64.b64encode(downloaded).decode('utf-8')
            content = [{"type":"image_url", "image_url":{"url":f"data:image/jpeg;base64,{b64}"}},
                       {"type":"text","text": message.caption or "Опиши"}]
        else:
            content = [{"type":"text","text": message.text}]

        chat_histories[user_id].append({"role":"user","content":content})
        save_json(HISTORY_FILE, chat_histories)

        completion = client.chat.completions.create(
            model=model_api,
            messages=chat_histories[user_id],
            temperature=s["temperature"]
        )

        response = completion.choices[0].message.content
        if "ОТВЕТ:" in response:
            response = response.split("ОТВЕТ:")[1]

        safe_delete(message.chat.id, loading.message_id)
        send_long_message(message.chat.id, response, reply_markup=main_menu_keyboard(user_id))

        chat_histories[user_id].append({"role":"assistant","content":[{"type":"text","text":response}]})
        save_json(HISTORY_FILE, chat_histories)

    except Exception as e:
        safe_delete(message.chat.id, loading.message_id)
        bot.send_message(message.chat.id, f"Ошибка: {e}", reply_markup=main_menu_keyboard(user_id))

print("BOT READY ✔")
bot.polling(non_stop=True)
