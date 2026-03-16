# Extractor/modules/utk_free.py

import os
import json
import time
import logging
import asyncio
import requests
from base64 import b64decode, b64encode
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad, pad
from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.enums import ParseMode

# ------- CONFIG -------
API_URL = "https://application.utkarshapp.com/index.php/data_model"
COMMON_KEY = b"%!^F&^$)&^$&*$^&"
COMMON_IV = b"#*v$JvywJvyJDyvJ"
key_chars = "%!F*&^$)_*%3f&B+"
iv_chars = "#*$DJvyw2w%!_-$@"

HEADERS = {
    "Authorization": "Bearer 152#svf346t45ybrer34yredk76t",
    "Content-Type": "text/plain; charset=UTF-8",
    "devicetype": "1",
    "host": "application.utkarshapp.com",
    "lang": "1",
    "user-agent": "okhttp/4.9.0",
    "userid": "0",
    "version": "152"
}

base_url = 'https://online.utkarsh.com/'
login_url = 'https://online.utkarsh.com/web/Auth/login'
tiles_data_url = 'https://online.utkarsh.com/web/Course/tiles_data'
layer_two_data_url = 'https://online.utkarsh.com/web/Course/get_layer_two_data'
meta_source_url = '/meta_distributer/on_request_meta_source'

# ------- GLOBAL STORAGE -------
user_sessions = {}  # {user_id: {"mobile": ..., "password": ...}}
progress_data = {}  # {user_id: {"videos": 0, "pdfs": 0, "others": 0, "total": 0, "start_time": ...}}

# ------- HELPERS -------
def encrypt(data, use_common_key, key, iv):
    cipher_key, cipher_iv = (COMMON_KEY, COMMON_IV) if use_common_key else (key.encode() if isinstance(key, str) else key, iv.encode() if isinstance(iv, str) else iv)
    cipher = AES.new(cipher_key, AES.MODE_CBC, cipher_iv)
    padded_data = pad(json.dumps(data, separators=(",", ":")).encode(), AES.block_size)
    encrypted = cipher.encrypt(padded_data)
    return b64encode(encrypted).decode() + ":"

def decrypt(data, use_common_key, key, iv):
    cipher_key, cipher_iv = (COMMON_KEY, COMMON_IV) if use_common_key else (key.encode() if isinstance(key, str) else key, iv.encode() if isinstance(iv, str) else iv)
    cipher = AES.new(cipher_key, AES.MODE_CBC, cipher_iv)
    try:
        encrypted_data = b64decode(data.split(":")[0])
        decrypted_bytes = cipher.decrypt(encrypted_data)
        decrypted = unpad(decrypted_bytes, AES.block_size).decode()
        return decrypted
    except:
        return None

def post_request(path, data=None, use_common_key=False, key=None, iv=None):
    encrypted_data = encrypt(data, use_common_key, key, iv) if data else None
    response = requests.post(f"{API_URL}{path}", headers=HEADERS, data=encrypted_data)
    decrypted_data = decrypt(response.text, use_common_key, key, iv)
    if decrypted_data:
        try:
            return json.loads(decrypted_data)
        except:
            pass
    return {}



def sanitize_filename(name: str) -> str:
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, '_')
    return name.strip('_')



def decrypt_stream(enc):
    try:
        enc = b64decode(enc)
        key = b"%!$!%_$&!%F)&^!^"
        iv = b"#*y*#2yJ*#$wJv*v"
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted_bytes = cipher.decrypt(enc)
        try:
            plaintext = unpad(decrypted_bytes, AES.block_size).decode('utf-8')
        except:
            plaintext = decrypted_bytes.decode('utf-8', errors='ignore')
        cleaned_json = ''
        for i in range(len(plaintext)):
            try:
                json.loads(plaintext[:i+1])
                cleaned_json = plaintext[:i+1]
            except:
                continue
        return cleaned_json
    except:
        return None

def decrypt_and_load_json(enc):
    try:
        return json.loads(decrypt_stream(enc))
    except:
        return {}

def encrypt_stream(plain_text):
    key = b"%!$!%_$&!%F)&^!^"
    iv = b"#*y*#2yJ*#$wJv*v"
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded_text = pad(plain_text.encode('utf-8'), AES.block_size)
    encrypted = cipher.encrypt(padded_text)
    return b64encode(encrypted).decode('utf-8')

# ------- EXTRACTION (SYNC) -------
def _extract_sync(mobile, password, batch_id, user_id):
    session = requests.Session()
    try:
        # 1. Get CSRF
        r1 = session.get(base_url)
        csrf_token = r1.cookies.get('csrf_name')
        if not csrf_token:
            return [], "❌ CSRF token missing."

        # 2. Login
        d1 = {
            'csrf_name': csrf_token,
            'mobile': mobile,
            'url': '0',
            'password': password,
            'submit': 'LogIn',
            'device_token': 'null'
        }
        h = {
            'Host': 'online.utkarsh.com',
            'Sec-Ch-Ua': '"Chromium";v="119", "Not?A_Brand";v="24"',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
            'Sec-Ch-Ua-Mobile': '?0',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.6045.199 Safari/537.36'
        }
        u2 = session.post(login_url, data=d1, headers=h).json()
        r2 = u2.get("response")
        dr1 = decrypt_and_load_json(r2)
        jwt = dr1.get("data", {}).get("jwt")
        if not jwt:
            return [], "❌ Login failed."
        h["jwt"] = jwt
        HEADERS["jwt"] = jwt

        # 3. Profile
        profile = post_request("/users/get_my_profile", use_common_key=True)
        user_id_num = profile.get("data", {}).get("id")
        if not user_id_num:
            return [], "❌ User ID not found."
        HEADERS["userid"] = user_id_num
        key = "".join(key_chars[int(i)] for i in (user_id_num + "1524567456436545")[:16]).encode()
        iv = "".join(iv_chars[int(i)] for i in (user_id_num + "1524567456436545")[:16]).encode()

        # 4. Fetch course tiles
        d3 = {"course_id": batch_id, "revert_api": "1#0#0#1", "parent_id": 0, "tile_id": "15330", "layer": 1, "type": "course_combo"}
        de1 = encrypt_stream(json.dumps(d3))
        d4 = {'tile_input': de1, 'csrf_name': csrf_token}
        u4 = session.post(tiles_data_url, headers=h, data=d4).json()
        r4 = u4.get("response")
        dr3 = decrypt_and_load_json(r4)
        data = dr3.get("data", [])

        # 5. Generate .txt files
        files = []
        for i in data:
            try:
                fi = i.get("id")
                tn = i.get("title", "Unknown").strip()
                binfo = i.get("segment_information", "")
                fn = f"{fi}_{sanitize_filename(tn)}.txt"
             #   fn = f"{fi}_{tn.replace('/', '_').replace(':', '_').replace('|', '_').replace('\\\\', '_')}.txt"
                with open(fn, "w", encoding="utf-8") as f:
                    f.write(f"{fi} ♧ {tn}\n{binfo}\n\n")
                    d5 = {"course_id": fi, "layer": 1, "page": 1, "parent_id": fi, "revert_api": "1#1#0#1", "tile_id": "0", "type": "content"}
                    de2 = encrypt_stream(json.dumps(d5))
                    d6 = {'tile_input': de2, 'csrf_name': csrf_token}
                    u5 = session.post(tiles_data_url, headers=h, data=d6).json()
                    r5 = u5.get("response")
                    dr4 = decrypt_and_load_json(r5)
                    for subj in dr4.get("data", {}).get("list", []):
                        sfi = subj.get("id")
                        d7 = {"course_id": fi, "parent_id": fi, "layer": 2, "page": 1, "revert_api": "1#0#0#1", "subject_id": sfi, "tile_id": 0, "topic_id": sfi, "type": "content"}
                        de3 = b64encode(json.dumps(d7).encode()).decode()
                        d8 = {'layer_two_input_data': de3, 'csrf_name': csrf_token}
                        u6 = session.post(layer_two_data_url, headers=h, data=d8).json()
                        r6 = u6.get("response")
                        dr5 = decrypt_and_load_json(r6)
                        for topic in dr5.get("data", {}).get("list", []):
                            ti = topic.get("id")
                            d9 = {"course_id": fi, "parent_id": fi, "layer": 3, "page": 1, "revert_api": "1#0#0#1", "subject_id": sfi, "tile_id": 0, "topic_id": ti, "type": "content"}
                            de4 = b64encode(json.dumps(d9).encode()).decode()
                            d10 = {'layer_two_input_data': de4, 'csrf_name': csrf_token}
                            u7 = session.post(layer_two_data_url, headers=h, data=d10).json()
                            r7 = u7.get("response")
                            dr6 = decrypt_and_load_json(r7)
                            for video in dr6.get("data", {}).get("list", []):
                                jt = video.get("title", "Video")
                                jti = video.get("payload", {}).get("tile_id")
                                if not jti:
                                    continue
                                j4 = {
                                    "course_id": fi,
                                    "device_id": "x",
                                    "device_name": "x",
                                    "download_click": "0",
                                    "name": f"{video.get('id', '')}_0_0",
                                    "tile_id": jti,
                                    "type": "video"
                                }
                                j5 = post_request(meta_source_url, j4, key=key, iv=iv)
                                cj = j5.get("data", {})
                                vu = ""
                                if isinstance(cj, dict):
                                    qo = cj.get("bitrate_urls", [])
                                    if isinstance(qo, list) and len(qo) >= 4:
                                        vu = qo[3].get("url") or qo[2].get("url") or qo[1].get("url") or qo[0].get("url")
                                    elif isinstance(qo, list) and qo:
                                        vu = qo[0].get("url", "")
                                    if not vu:
                                        vu = cj.get("link", "")
                                elif isinstance(cj, list) and cj and isinstance(cj[0], dict):
                                    first = cj[0]
                                    qo = first.get("bitrate_urls", [])
                                    if isinstance(qo, list) and len(qo) >= 4:
                                        vu = qo[3].get("url") or qo[2].get("url") or qo[1].get("url") or qo[0].get("url")
                                    elif qo:
                                        vu = qo[0].get("url", "")
                                    if not vu:
                                        vu = first.get("link", "")

                                if vu:
                                    pu = vu.split("?Expires=")[0] if "?Expires=" in vu else vu
                                    if not pu.startswith("http"):
                                        pu = f"https://www.youtube.com/embed/{pu}"
                                    line = f"{jt}: {pu}\n"
                                    f.write(line)
                                    progress_data[user_id]["videos"] += 1
                                    progress_data[user_id]["total"] += 1
                files.append(fn)
            except Exception as e:
                logging.warning(f"Skip item: {e}")
                continue
        return files, None
    except Exception as e:
        logging.exception("Fatal error")
        return [], f"💥 {str(e)[:150]}"

# ------- ASYNC WRAPPER -------
async def extract_courses(mobile, password, batch_id, user_id):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _extract_sync, mobile, password, batch_id, user_id)

# ------- PROGRESS UPDATER (TASK) -------
async def update_progress_task(client: Client, msg: Message, user_id: int):
    start_time = progress_data[user_id]["start_time"]
    while True:
        await asyncio.sleep(30)
        try:
            mins = int((time.time() - start_time) / 60)
            await msg.edit_text(
                "⏳ Extraction running...\n"
                f"⏱ Time: {mins} min\n\n"
                f"🎥 Videos: {progress_data[user_id]['videos']}\n"
                f"📄 PDFs: {progress_data[user_id]['pdfs']}\n"
                f"📦 Others: {progress_data[user_id]['others']}\n"
                f"🔢 Total: {progress_data[user_id]['total']}"
            )
        except:
            break

# ------- MAIN HANDLER -------
async def handle_utk_free(app: Client, message: Message):
    user_id = message.from_user.id

    # Ask for credentials
    await message.reply_text("📱 Send: **mobile*password**\n*(e.g. 9876543210*MyPass123)*", parse_mode=ParseMode.MARKDOWN)

    # Wait for reply with mobile*password
    try:
        cred_msg = await app.listen(chat_id=user_id, timeout=120)
        if not cred_msg.text or "*" not in cred_msg.text:
            await message.reply_text("❌ Invalid format. Use: mobile*password")
            return
        mobile, password = cred_msg.text.strip().split("*", 1)
        user_sessions[user_id] = {"mobile": mobile, "password": password}
    except asyncio.TimeoutError:
        await message.reply_text("⏰ Timed out. Please start again.")
        return

    # Ask for Batch ID
    await message.reply_text("✅ Credentials saved.\n📥 Send **Batch ID** (Course ID):")

    try:
        batch_msg = await app.listen(chat_id=user_id, timeout=120)
        batch_id = batch_msg.text.strip()
    except asyncio.TimeoutError:
        await message.reply_text("⏰ Timed out. Please start again.")
        return

    # Initialize progress tracker
    progress_data[user_id] = {
        "videos": 0,
        "pdfs": 0,
        "others": 0,
        "total": 0,
        "start_time": time.time()
    }

    status_msg = await message.reply_text("⏳ Starting extraction...")

    # Start progress updater task
    progress_task = asyncio.create_task(update_progress_task(app, status_msg, user_id))

    # Run extraction
    files, err = await extract_courses(mobile, password, batch_id, user_id)

    # Cancel progress task
    progress_task.cancel()

    if err:
        await status_msg.edit_text(f"❌ {err}")
        return

    if not files:
        await status_msg.edit_text("⚠️ No content found.")
        return

    # Send files
    for fn in files:
        try:
            await message.reply_document(document=fn)
        except Exception as e:
            await message.reply_text(f"⚠️ Failed to send {fn}: {str(e)}")
        if os.path.exists(fn):
            os.remove(fn)

    await status_msg.edit_text("✅ All .txt files sent!")
