import telebot
from telebot import types
from openai import OpenAI
import json
import os
import base64
from pathlib import Path
import hashlib
from typing import Dict, Any, List

# Для мультимодальных моделей
try:
    from transformers import AutoTokenizer
except Exception as e:
    raise ImportError("Установите transformers: pip install transformers tokenizers") from e

# ========== CONFIG ==========
API_TOKEN = "8057312342:AAEpPXaXZdgWyfTOK3IAeTIChDNZy6pUKP0"
HISTORY_FILE = 'history.json'
SETTINGS_FILE = 'settings.json'
IMAGES_DIR = Path("saved_images")
IMAGES_DIR.mkdir(exist_ok=True)

AVAILABLE_MODELS = {
    "ministral": "mistralai/ministral-3-14b-reasoning",
    "qwen_vl": "qwen/qwen3-vl-30b",
    "local_default": "gpt2"
}

BASE_URL = "http://localhost:1234/v1"
MAX_TOKENS = 16384
AUTO_CLEAR_THRESHOLD = int(MAX_TOKENS * 1.05)
IMAGE_TOKEN_COST = 50

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

# ========== STORAGE ==========
def load_json(filename, default):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k) if k.isdigit() else k: v for k,v in data.items()}
        except:
            return default
    return default

def save_json(filename, data):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

chat_histories: Dict[Any, List[Dict[str, Any]]] = load_json(HISTORY_FILE, {})
user_settings: Dict[Any, Dict[str, Any]] = load_json(SETTINGS_FILE, {})

# ========== TOKENIZER ==========
_tokenizer_cache: Dict[str, Any] = {}

def get_tokenizer(model_name: str):
    if model_name in _tokenizer_cache:
        return _tokenizer_cache[model_name]
    try:
        tok = AutoTokenizer.from_pretrained(model_name, use_fast=True)
    except:
        tok = AutoTokenizer.from_pretrained("gpt2", use_fast=True)
    _tokenizer_cache[model_name] = tok
    return tok

def text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except:
        return str(content)

def count_tokens(text: str, tokenizer) -> int:
    if not text:
        return 0
    MAX_CHARS = 20000
    if len(text) > MAX_CHARS:
        return max(1,len(text)//4)
    try:
        return len(tokenizer.encode(text, add_special_tokens=False))
    except:
        return max(1,len(text)//4)

def count_tokens_history(user_id, model_name: str) -> int:
    hist = chat_histories.get(user_id, [])
    if not hist:
        return 0
    tokenizer = get_tokenizer(model_name)
    total = 0
    for msg in hist:
        content = msg.get("content","")
        if isinstance(content, dict) and content.get("type") == "image":
            total += IMAGE_TOKEN_COST
            continue
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    total += count_tokens(text_from_content(item["text"]), tokenizer)
                else:
                    total += count_tokens(text_from_content(item), tokenizer)
        else:
            total += count_tokens(text_from_content(content), tokenizer)
    return total

def enforce_token_limit(user_id, model_name: str):
    trimmed = False
    if user_id not in chat_histories or not chat_histories[user_id]:
        init_history(user_id)
        return trimmed
    total_tokens = count_tokens_history(user_id, model_name)
    if total_tokens <= MAX_TOKENS:
        return trimmed
    if total_tokens > AUTO_CLEAR_THRESHOLD:
        init_history(user_id)
        save_json(HISTORY_FILE, chat_histories)
        return True
    while total_tokens > MAX_TOKENS and len(chat_histories[user_id]) > 1:
        chat_histories[user_id].pop(1)
        total_tokens = count_tokens_history(user_id, model_name)
        trimmed = True
    save_json(HISTORY_FILE, chat_histories)
    return trimmed

def history_percent(user_id, model_name: str) -> int:
    t = count_tokens_history(user_id, model_name)
    return min(int((t / MAX_TOKENS) * 100), 100)

def progress_bar(perc, size=12):
    filled = int((perc / 100) * size)
    return "▓"*filled + "░"*(size-filled)

# ========== SETTINGS / HISTORY ==========
def get_settings(user_id):
    default_cfg = {"role":"default","temperature":0.7,"model":"local_default"}
    if user_id not in user_settings:
        user_settings[user_id] = default_cfg.copy()
    else:
        for k,v in default_cfg.items():
            if k not in user_settings[user_id]:
                user_settings[user_id][k] = v
    save_json(SETTINGS_FILE, user_settings)
    return user_settings[user_id]

def get_system_prompt(user_id):
    s = get_settings(user_id)
    return ROLES.get(s["role"],ROLES["default"]) + "\n" + THINKING_INSTRUCTION

def init_history(user_id):
    chat_histories[user_id] = [{"role":"system","content":get_system_prompt(user_id)}]
    save_json(HISTORY_FILE, chat_histories)

# ========== IMAGE HELPERS ==========
def save_image(bytes_data: bytes, ext="jpg") -> str:
    h = hashlib.sha256(bytes_data).hexdigest()
    path = IMAGES_DIR / f"{h}.{ext}"
    if not path.exists():
        path.write_bytes(bytes_data)
    return str(path)

def build_messages_for_api(history_list):
    msgs = []
    for msg in history_list:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, dict) and content.get("type") == "image":
            # LM Studio понимает только image_url
            msgs.append({
                "role": role,
                "content": {
                    "type": "image_url",
                    "image_url": f"file://{content.get('path')}"  # или прямой URL
                }
            })
            # можно добавить подпись отдельным текстом, если нужно:
            if content.get("caption"):
                msgs.append({
                    "role": role,
                    "content": {
                        "type": "text",
                        "text": content.get("caption")
                    }
                })
        else:
            # обычный текст
            msgs.append({
                "role": role,
                "content": {
                    "type": "text",
                    "text": text_from_content(content)
                }
            })
    return msgs


# ========== KEYBOARDS ==========
def main_menu_keyboard(user_id):
    s = get_settings(user_id)
    model_name = AVAILABLE_MODELS.get(s["model"],"gpt2")
    perc = history_percent(user_id, model_name)
    bar = progress_bar(perc)
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("New chat 🗑", callback_data="new_chat"),
        types.InlineKeyboardButton(f"Context {perc}% {bar}", callback_data="show_history")
    )
    markup.add(
        types.InlineKeyboardButton(f"Role 🎭 ({s['role']})", callback_data="menu_roles"),
        types.InlineKeyboardButton(f"Model 🤖 ({s['model']})", callback_data="menu_models")
    )
    markup.add(
        types.InlineKeyboardButton(f"Temperature 🌡 ({s['temperature']})", callback_data="menu_temp")
    )
    return markup

def roles_keyboard(user_id):
    current = get_settings(user_id)["role"]
    markup = types.InlineKeyboardMarkup()
    for r in ROLES.keys():
        markup.add(types.InlineKeyboardButton(f"{r} {'✔' if r==current else ''}",callback_data=f"set_role_{r}"))
    markup.add(types.InlineKeyboardButton("⬅ Back",callback_data="main_menu"))
    return markup

def models_keyboard(user_id):
    current = get_settings(user_id)["model"]
    markup = types.InlineKeyboardMarkup()
    for m in AVAILABLE_MODELS.keys():
        markup.add(types.InlineKeyboardButton(f"{m} {'✔' if m==current else ''}",callback_data=f"set_model_{m}"))
    markup.add(types.InlineKeyboardButton("⬅ Back",callback_data="main_menu"))
    return markup

def temp_keyboard(user_id):
    current = get_settings(user_id)["temperature"]
    markup = types.InlineKeyboardMarkup()
    for t in ["0.1","0.3","0.7","1.0"]:
        markup.add(types.InlineKeyboardButton(f"{t} {'✔' if float(t)==current else ''}",callback_data=f"set_temp_{t}"))
    markup.add(types.InlineKeyboardButton("⬅ Back",callback_data="main_menu"))
    return markup

# ========== START / CALLBACKS ==========
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    get_settings(user_id)
    bot.reply_to(message,"Панель управления:",reply_markup=main_menu_keyboard(user_id))

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    s = get_settings(user_id)
    model_name = AVAILABLE_MODELS.get(s["model"],"gpt2")
    if call.data=="main_menu":
        try:
            bot.edit_message_text("Меню:", call.message.chat.id, call.message.message_id,reply_markup=main_menu_keyboard(user_id))
        except: bot.send_message(call.message.chat.id,"Меню:",reply_markup=main_menu_keyboard(user_id))
    elif call.data=="new_chat":
        init_history(user_id)
        bot.send_message(call.message.chat.id,"История полностью очищена!",reply_markup=main_menu_keyboard(user_id))
    elif call.data=="menu_models":
        try:
            bot.edit_message_text("Выберите модель:", call.message.chat.id, call.message.message_id,reply_markup=models_keyboard(user_id))
        except: bot.send_message(call.message.chat.id,"Выберите модель:",reply_markup=models_keyboard(user_id))
    elif call.data.startswith("set_model_"):
        s["model"]=call.data.replace("set_model_","")
        save_json(SETTINGS_FILE,user_settings)
        bot.edit_message_text("Модель установлена.",call.message.chat.id,call.message.message_id,reply_markup=main_menu_keyboard(user_id))
    elif call.data=="menu_roles":
        bot.edit_message_text("Выберите роль:",call.message.chat.id,call.message.message_id,reply_markup=roles_keyboard(user_id))
    elif call.data.startswith("set_role_"):
        s["role"]=call.data.replace("set_role_","")
        save_json(SETTINGS_FILE,user_settings)
        init_history(user_id)
        bot.edit_message_text("Роль применена!",call.message.chat.id,call.message.message_id,reply_markup=main_menu_keyboard(user_id))
    elif call.data=="menu_temp":
        bot.edit_message_text("Температура:",call.message.chat.id,call.message.message_id,reply_markup=temp_keyboard(user_id))
    elif call.data.startswith("set_temp_"):
        s["temperature"]=float(call.data.replace("set_temp_",""))
        save_json(SETTINGS_FILE,user_settings)
        bot.edit_message_text("Температура обновлена!",call.message.chat.id,call.message.message_id,reply_markup=main_menu_keyboard(user_id))
    elif call.data=="show_history":
        hist=chat_histories.get(user_id,[])
        perc=history_percent(user_id,model_name)
        bar=progress_bar(perc)
        status="🟢 Свободно" if perc<70 else "🟡 Нагружено" if perc<100 else "🔴 Переполнено!"
        bot.send_message(call.message.chat.id,f"📜 В памяти сообщений: {len(hist)}\n📊 Контекст: {bar} {perc}%\n⚡ Статус: {status}\n🔢 Токенов (прибл.): {count_tokens_history(user_id,model_name)} / {MAX_TOKENS}",reply_markup=main_menu_keyboard(user_id))

# ========== MESSAGE HANDLER ==========
@bot.message_handler(content_types=['text','photo'])
def handle_message(message):
    user_id=message.from_user.id
    if user_id not in chat_histories: init_history(user_id)
    s=get_settings(user_id)
    model_name=AVAILABLE_MODELS.get(s["model"],"gpt2")
    enforce_token_limit(user_id,model_name)
    loading=bot.reply_to(message,"⏳ Думаю...")
    try:
        if message.photo:
            file_info=bot.get_file(message.photo[-1].file_id)
            downloaded=bot.download_file(file_info.file_path)
            local_path=save_image(downloaded,"jpg")
            user_content={"type":"image","path":local_path,"caption":message.caption or "Опиши картинку"}
        else:
            user_content=message.text

        chat_histories[user_id].append({"role":"user","content":user_content})
        save_json(HISTORY_FILE,chat_histories)
        enforce_token_limit(user_id,model_name)

        messages_for_api=build_messages_for_api(chat_histories[user_id])
        completion=client.chat.completions.create(
            model=AVAILABLE_MODELS.get(s["model"],"local-model"),
            messages=messages_for_api,
            temperature=s["temperature"]
        )
        response=completion.choices[0].message.content
        if "ОТВЕТ:" in response:
            response=response.split("ОТВЕТ:")[1]

        bot.delete_message(message.chat.id,loading.message_id)
        bot.send_message(message.chat.id,response,reply_markup=main_menu_keyboard(user_id))
        chat_histories[user_id].append({"role":"assistant","content":response})
        save_json(HISTORY_FILE,chat_histories)
        enforce_token_limit(user_id,model_name)

    except Exception as e:
        try: bot.delete_message(message.chat.id,loading.message_id)
        except: pass
        bot.send_message(message.chat.id,f"Ошибка: {e}",reply_markup=main_menu_keyboard(user_id))

print("BOT READY ✔")
bot.polling(non_stop=True)
