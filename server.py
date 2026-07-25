import os
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
import telebot

app = Flask(__name__)
# ফ্রন্টএন্ড (HTML) থেকে রিকোয়েস্ট অ্যালাউ করার জন্য CORS
CORS(app)

# কোন কোন বট চলছে তার রেকর্ড রাখার ডিকশনারি
active_bots = {}

def poll_bot(token, welcome_message):
    try:
        bot = telebot.TeleBot(token)
        
        @bot.message_handler(commands=['start'])
        def send_welcome(message):
            bot.reply_to(message, welcome_message)

        @bot.message_handler(func=lambda message: True)
        def echo_all(message):
            bot.reply_to(message, "TeleCraft থেকে বলছি! আপনার মেসেজ: " + message.text)
        
        # বট কন্টিনিউয়াস চালু রাখার জন্য
        print(f"[+] Bot Started: {token[:10]}...")
        bot.polling(non_stop=True)
    except Exception as e:
        print(f"[-] Error with bot {token[:10]}: {e}")

@app.route('/', methods=['GET'])
def home():
    return jsonify({"status": "Server is running perfectly on Render!"})

@app.route('/start_bot', methods=['POST'])
def start_bot():
    data = request.json
    token = data.get('token')
    welcome_message = data.get('welcome_message', 'Hello from TeleCraft Real Bot!')

    if not token:
        return jsonify({"success": False, "message": "টোকেন দেওয়া হয়নি!"}), 400

    if token in active_bots:
        return jsonify({"success": False, "message": "বটটি আগে থেকেই চলছে!"})

    # মূল সার্ভার যেন আটকে না যায়, তাই বটকে আলাদা থ্রেডে রান করানো হচ্ছে
    thread = threading.Thread(target=poll_bot, args=(token, welcome_message))
    thread.daemon = True
    thread.start()
    
    active_bots[token] = thread
    return jsonify({"success": True, "message": "টেলিগ্রাম বট সফলভাবে চালু হয়েছে!"})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    data = request.json
    token = data.get('token')

    if token in active_bots:
        del active_bots[token]
        return jsonify({"success": True, "message": "বট বন্ধ করা হয়েছে (সিস্টেম থেকে রিমুভড)!"})
    
    return jsonify({"success": False, "message": "বটটি রানিং নেই!"})

if __name__ == '__main__':
    # Render সাধারণত PORT এনভায়রনমেন্ট ভ্যারিয়েবল সেট করে দেয়
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
