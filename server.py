import os, asyncio, threading, time, logging
from typing import Dict, Any
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
logging.basicConfig(level=logging.INFO)

BOTS: Dict[str, Dict[str, Any]] = {}
LOCK = threading.RLock()


def tg(token, method, payload=None, timeout=25):
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload or {}, timeout=timeout)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram API error"))
    return data["result"]


def validate_token(token):
    if not isinstance(token, str) or ":" not in token or len(token) < 20:
        raise ValueError("Invalid Telegram bot token format")
    return tg(token, "getMe")


def worker(bot_id):
    with LOCK:
        state = BOTS.get(bot_id)
        if not state:
            return
        token = state["token"]
        state["running"] = True
        state["last_error"] = None
    offset = None
    try:
        tg(token, "deleteWebhook", {"drop_pending_updates": False})
        while True:
            with LOCK:
                if not BOTS.get(bot_id, {}).get("running"):
                    break
                nodes = list(BOTS[bot_id].get("nodes", []))
            payload = {"timeout": 20, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                payload["offset"] = offset
            updates = tg(token, "getUpdates", payload, timeout=30)
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    process_update(token, nodes, u)
                    with LOCK:
                        BOTS[bot_id]["executions"] += 1
                except Exception as e:
                    logging.exception("Update error")
                    with LOCK:
                        BOTS[bot_id]["last_error"] = str(e)
    except Exception as e:
        logging.exception("Bot stopped with error")
        with LOCK:
            if bot_id in BOTS:
                BOTS[bot_id]["last_error"] = str(e)
                BOTS[bot_id]["running"] = False
    finally:
        with LOCK:
            if bot_id in BOTS:
                BOTS[bot_id]["running"] = False


def process_update(token, nodes, update):
    msg = update.get("message")
    if not msg:
        return
    text = msg.get("text", "") or ""
    chat_id = msg["chat"]["id"]
    matched = False
    for i, node in enumerate(nodes):
        if node.get("type") != "trigger_command":
            continue
        cmd = node.get("command", "/start").split()[0]
        if text.split()[0] == cmd if text else False:
            matched = True
            execute_actions(token, chat_id, text, nodes[i+1:])
            break
    if not matched:
        for node in nodes:
            if node.get("type") == "trigger_any_message":
                execute_actions(token, chat_id, text, nodes)
                break


def execute_actions(token, chat_id, user_text, nodes):
    for node in nodes:
        t = node.get("type")
        if t.startswith("trigger_"):
            break
        if t == "action_message":
            tg(token, "sendMessage", {"chat_id": chat_id, "text": node.get("text", "")})
        elif t == "action_photo":
            tg(token, "sendPhoto", {"chat_id": chat_id, "photo": node.get("url", "")})
        elif t == "action_video":
            tg(token, "sendVideo", {"chat_id": chat_id, "video": node.get("url", "")})
        elif t == "action_audio":
            tg(token, "sendAudio", {"chat_id": chat_id, "audio": node.get("url", "")})
        elif t == "action_document":
            tg(token, "sendDocument", {"chat_id": chat_id, "document": node.get("url", "")})
        elif t == "action_sticker":
            tg(token, "sendSticker", {"chat_id": chat_id, "sticker": node.get("sticker_id", "")})
        elif t == "action_chat_action":
            tg(token, "sendChatAction", {"chat_id": chat_id, "action": node.get("action", "typing")})
        elif t == "action_delete_message":
            tg(token, "deleteMessage", {"chat_id": chat_id, "message_id": node.get("message_id", 0)})
        elif t == "action_pin_message":
            tg(token, "pinChatMessage", {"chat_id": chat_id, "message_id": node.get("message_id", 0), "disable_notification": True})
        elif t == "action_unpin_message":
            tg(token, "unpinChatMessage", {"chat_id": chat_id, "message_id": node.get("message_id", 0)})
        elif t == "action_ai":
            reply = ai_reply(node, user_text)
            tg(token, "sendMessage", {"chat_id": chat_id, "text": reply})
        elif t == "action_api":
            url = node.get("url", "")
            r = requests.get(url, timeout=15)
            if node.get("reply_response"):
                tg(token, "sendMessage", {"chat_id": chat_id, "text": r.text[:4000]})
        elif t == "action_delay":
            time.sleep(min(max(float(node.get("seconds", 1)), 0), 30))
        elif t == "action_location":
            tg(token, "sendLocation", {"chat_id": chat_id, "latitude": float(node.get("latitude", 0)), "longitude": float(node.get("longitude", 0))})
        elif t == "action_contact":
            tg(token, "sendContact", {"chat_id": chat_id, "phone_number": node.get("phone", ""), "first_name": node.get("first_name", "Contact")})
        elif t == "action_dice":
            tg(token, "sendDice", {"chat_id": chat_id, "emoji": node.get("emoji", "🎲")})
        elif t == "action_poll":
            tg(token, "sendPoll", {"chat_id": chat_id, "question": node.get("question", "Poll"), "options": [{"text": x} for x in node.get("options", ["Yes", "No"])]})
        elif t == "action_chat_invite":
            link = tg(token, "exportChatInviteLink", {"chat_id": chat_id})
            tg(token, "sendMessage", {"chat_id": chat_id, "text": link})


def ai_reply(node, user_text):
    provider = os.getenv("AI_PROVIDER", "huggingface").lower()
    prompt = f"You are {node.get('persona','Helpful Assistant')}. {node.get('prompt','Answer clearly.') }\nUser: {user_text}\nAssistant:"
    if provider == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY", "")
        model = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-20b:free")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "user", "content": prompt}]}, timeout=45)
        data = r.json()
        if r.status_code >= 400:
            raise RuntimeError(data.get("error", {}).get("message", "OpenRouter error"))
        return data["choices"][0]["message"]["content"].strip()
    key = os.getenv("HF_TOKEN", "")
    model = os.getenv("HF_MODEL", "HuggingFaceH4/zephyr-7b-beta")
    if not key:
        raise RuntimeError("HF_TOKEN is not configured")
    r = requests.post(f"https://api-inference.huggingface.co/models/{model}", headers={"Authorization": f"Bearer {key}"}, json={"inputs": prompt, "parameters": {"max_new_tokens": 180, "temperature": 0.7}}, timeout=60)
    data = r.json()
    if r.status_code >= 400 or isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data.get("error", "Hugging Face error"))
    text = data[0].get("generated_text", "") if isinstance(data, list) else str(data)
    return text.split("Assistant:")[-1].strip()[:4000]


@app.get("/")
def home():
    return jsonify({"ok": True, "service": "TeleCraft Backend", "bots": len(BOTS)})

@app.get("/health")
def health():
    return jsonify({"ok": True, "running_bots": sum(1 for x in BOTS.values() if x.get("running"))})

@app.post("/validate_token")
def validate():
    try:
        token = request.json.get("token", "")
        return jsonify({"ok": True, "bot": validate_token(token)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.post("/start_bot")
def start_bot():
    data = request.get_json(force=True)
    bot_id = str(data.get("bot_id", ""))
    token = str(data.get("token", ""))
    if not bot_id or not token:
        return jsonify({"ok": False, "error": "bot_id and token are required"}), 400
    try:
        me = validate_token(token)
        with LOCK:
            old = BOTS.get(bot_id)
            if old and old.get("running"):
                return jsonify({"ok": True, "running": True, "bot": old.get("me", me)})
            BOTS[bot_id] = {"token": token, "nodes": data.get("nodes", []), "running": True, "executions": 0, "last_error": None, "me": me}
        threading.Thread(target=worker, args=(bot_id,), daemon=True).start()
        return jsonify({"ok": True, "running": True, "bot": me})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.post("/update_bot")
def update_bot():
    data = request.get_json(force=True)
    bot_id = str(data.get("bot_id", ""))
    with LOCK:
        if bot_id not in BOTS:
            return jsonify({"ok": False, "error": "Bot is not running"}), 404
        BOTS[bot_id]["nodes"] = data.get("nodes", [])
    return jsonify({"ok": True})

@app.post("/stop_bot")
def stop_bot():
    data = request.get_json(force=True)
    bot_id = str(data.get("bot_id", ""))
    with LOCK:
        if bot_id in BOTS:
            BOTS[bot_id]["running"] = False
    return jsonify({"ok": True, "running": False})

@app.post("/preview")
def preview():
    data = request.get_json(force=True)
    token = data.get("token", "")
    nodes = data.get("nodes", [])
    text = data.get("text", "")
    if not token:
        return jsonify({"ok": False, "error": "Bot token required"}), 400
    # Preview uses the same flow semantics but never sends Telegram messages.
    if text.startswith("/"):
        for i, n in enumerate(nodes):
            if n.get("type") == "trigger_command" and n.get("command") == text:
                replies = []
                for a in nodes[i+1:]:
                    if a.get("type", "").startswith("trigger_"): break
                    if a.get("type") == "action_message": replies.append(a.get("text", ""))
                    elif a.get("type") == "action_ai": replies.append(ai_reply(a, text))
                return jsonify({"ok": True, "replies": replies})
    for n in nodes:
        if n.get("type") == "action_ai":
            return jsonify({"ok": True, "replies": [ai_reply(n, text)]})
    return jsonify({"ok": True, "replies": ["No matching command or AI action found."]})

@app.get("/bot_status/<bot_id>")
def bot_status(bot_id):
    with LOCK:
        b = BOTS.get(bot_id)
        if not b:
            return jsonify({"ok": True, "running": False, "executions": 0})
        return jsonify({"ok": True, "running": bool(b.get("running")), "executions": b.get("executions", 0), "last_error": b.get("last_error")})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
