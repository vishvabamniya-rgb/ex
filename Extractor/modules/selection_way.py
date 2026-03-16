# Extractor/modules/selection_way.py

import os
import re
import tempfile
from pathlib import Path
import requests
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from Extractor import app
from config import join
import pytz
from datetime import datetime

# ---------------- CONFIG ----------------
BASE_URL = "https://backend.multistreaming.site/api"
USER_ID_FOR_ACTIVE = "1448640"
PAGE_SIZE = 10

BASE_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# ---------------- TIME SETUP ----------------
india_timezone = pytz.timezone('Asia/Kolkata')
time_new = datetime.now(india_timezone).strftime("%d-%m-%Y %I:%M %p")

# ---------------- GENERIC HELPERS ----------------
def safe_json_get(r: requests.Response):
    try:
        return r.json()
    except Exception as e:
        logging.warning("safe_json_get failed: %s", e)
        return {}

# ---------------- BATCH FETCHING ----------------
def get_active_batches():
    url = f"{BASE_URL}/courses?userId={USER_ID_FOR_ACTIVE}"
    try:
        r = requests.get(url, headers=BASE_HEADERS, timeout=15)
        data = safe_json_get(r)
        if isinstance(data, dict) and data.get("state") == 200 and isinstance(data.get("data"), list):
            return data["data"]
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]
        return []
    except Exception:
        logging.exception("get_active_batches error")
        return []

# ---------------- COURSE / CLASS HELPERS ----------------
def get_course_classes(course_id):
    url = f"{BASE_URL}/courses/{course_id}/classes?populate=full"
    try:
        r = requests.get(url, headers=BASE_HEADERS, timeout=20)
        data = safe_json_get(r)
        if isinstance(data, dict) and data.get("state") == 200 and isinstance(data.get("data"), list):
            return data["data"]
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            inner = data["data"]
            if "classes" in inner and isinstance(inner["classes"], list):
                return inner["classes"]
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]
        return []
    except Exception:
        logging.exception("get_course_classes error")
        return []

def find_pdf_from_active(course_id, batches=None):
    try:
        if batches is None:
            batches = get_active_batches()
        for b in batches:
            if str(b.get("id")) == str(course_id) or str(b.get("_id")) == str(course_id):
                pdf = b.get("batchInfoPdfUrl") or b.get("batch_info_pdf") or b.get("pdf") or ""
                if not pdf:
                    return []
                if isinstance(pdf, list):
                    return [p for p in pdf if p]
                if isinstance(pdf, str):
                    parts = re.split(r"[\n,;]+", pdf)
                    return [p.strip() for p in parts if p.strip()]
        return []
    except Exception:
        logging.exception("find_pdf_from_active error")
        return []

def _extract_subject_from_title(title, fallback=None):
    try:
        if "||" in title:
            parts = [p.strip() for p in title.split("||")]
            if len(parts) > 1:
                second = parts[1]
                if "|" in second:
                    return second.split("|")[0].strip()
                return second.strip()
        if "|" in title:
            parts = [p.strip() for p in title.split("|")]
            for p in parts:
                if p and not re.search(r"(?i)class[\s-]*\d+", p):
                    return p
        return fallback or "Course"
    except Exception:
        return fallback or "Course"

def normalize_video_entries(class_item):
    title = (
        class_item.get("title")
        or class_item.get("classTitle")
        or class_item.get("name")
        or class_item.get("heading")
        or "Untitled"
    )

    candidate_links = []

    direct_keys = [
        "class_link", "videoLink", "video_link", "video_url", "videoUrl",
        "link", "url", "playbackUrl", "playback_url", "streamUrl", "stream_url"
    ]
    for k in direct_keys:
        v = class_item.get(k)
        if isinstance(v, str) and v:
            candidate_links.append(v)

    m3u8_keys = [
        "masterPlaylist", "master_playlist",
        "hlsLink", "hls_link",
        "secureLink", "secure_link",
        "m3u8", "m3u8Url", "m3u8_url",
        "playlist", "playlistUrl"
    ]
    for k in m3u8_keys:
        v = class_item.get(k)
        if isinstance(v, str) and v:
            candidate_links.append(v)

    array_keys = ["rawSources", "sources", "recordings", "files", "videoFiles", "videos", "assets"]
    for k in array_keys:
        arr = class_item.get(k)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, str) and it:
                    candidate_links.append(it)
                elif isinstance(it, dict):
                    for subk in ("url", "file", "src", "mp4", "m3u8"):
                        vv = it.get(subk)
                        if isinstance(vv, str) and vv:
                            candidate_links.append(vv)

    nested_keys = ["playback", "video", "stream", "media"]
    for nk in nested_keys:
        obj = class_item.get(nk)
        if isinstance(obj, dict):
            for subk in ("url", "file", "m3u8", "mp4", "hls", "src"):
                vv = obj.get(subk)
                if isinstance(vv, str) and vv:
                    candidate_links.append(vv)
        elif isinstance(obj, list):
            for it in obj:
                if isinstance(it, str):
                    candidate_links.append(it)
                elif isinstance(it, dict):
                    for subk in ("url", "file", "src", "mp4", "m3u8"):
                        vv = it.get(subk)
                        if isinstance(vv, str):
                            candidate_links.append(vv)

    for k in ("embed", "iframe", "embedHtml"):
        v = class_item.get(k)
        if isinstance(v, str) and "http" in v:
            m = re.search(r"https?://[^\s'\"<>]+", v)
            if m:
                candidate_links.append(m.group(0))

    seen = set()
    clean_candidates = []
    for u in candidate_links:
        if not isinstance(u, str) or not u.strip():
            continue
        u = u.strip()
        if u not in seen:
            seen.add(u)
            clean_candidates.append(u)

    hls_links = [u for u in clean_candidates if "m3u8" in u or "playlist-mpl" in u or "hls" in u.lower()]
    other_links = [u for u in clean_candidates if u not in hls_links]

    mp4_list = []
    for u in clean_candidates:
        low = u.lower()
        if low.endswith(".mp4") or ".mp4?" in low:
            mp4_list.append(u)

    explicit_mp4 = class_item.get("mp4Recordings") or class_item.get("mp4_recordings") or class_item.get("mp4records")
    if isinstance(explicit_mp4, list):
        for it in explicit_mp4:
            if isinstance(it, str) and it.strip():
                if it not in mp4_list:
                    mp4_list.append(it.strip())
            elif isinstance(it, dict):
                for subk in ("url", "file", "mp4"):
                    vv = it.get(subk)
                    if isinstance(vv, str) and vv.strip() and vv not in mp4_list:
                        mp4_list.append(vv.strip())

    mp4_seen = set()
    mp4_clean = []
    for m in mp4_list:
        if m not in mp4_seen:
            mp4_seen.add(m)
            mp4_clean.append(m)

    class_pdfs = []
    pdf_keys = ["classPdf", "class_pdf", "pdfs", "materials", "resources", "files"]
    for key in pdf_keys:
        arr = class_item.get(key)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, str) and ".pdf" in it.lower():
                    class_pdfs.append(it.strip())
                elif isinstance(it, dict):
                    for subk in ("url", "file", "pdf"):
                        vv = it.get(subk)
                        if isinstance(vv, str) and ".pdf" in vv.lower():
                            class_pdfs.append(vv.strip())

    for k in ("pdf", "pdfUrl", "pdf_url", "file"):
        v = class_item.get(k)
        if isinstance(v, str) and ".pdf" in v.lower():
            class_pdfs.append(v.strip())

    pdf_seen = set()
    pdf_clean = []
    for p in class_pdfs:
        if p not in pdf_seen:
            pdf_seen.add(p)
            pdf_clean.append(p)

    primary_link = hls_links[0] if hls_links else (other_links[0] if other_links else "")
    include_mp4s = not (primary_link and ("m3u8" in primary_link or "hls" in primary_link.lower() or "playlist-mpl" in primary_link))

    return {
        "title": title,
        "class_link": primary_link,
        "mp4Recordings": mp4_clean if include_mp4s else [],
        "classPdf": pdf_clean
    }

def build_txt_for_course(course_id, course_title=None):
    classes = get_course_classes(course_id)
    batches = get_active_batches()

    if not classes:
        return False, "ERROR: Failed to fetch classes.", {}

    items_to_process = []
    try:
        if isinstance(classes, list) and classes and isinstance(classes[0], dict) and classes[0].get("topicName") and classes[0].get("classes"):
            for topic_block in classes:
                for cls in topic_block.get("classes", []):
                    items_to_process.append(cls)
        else:
            items_to_process = classes if isinstance(classes, list) else []
    except Exception:
        items_to_process = classes if isinstance(classes, list) else []

    lines = []
    total_videos = total_mp4 = total_m3u8 = total_youtube = total_pdfs = 0

    for cls in items_to_process:
        normalized = normalize_video_entries(cls)
        title = normalized.get("title", "Untitled")
        subject = _extract_subject_from_title(title, fallback=(course_title or "Course"))

        primary = normalized.get("class_link") or ""
        if primary:
            lines.append(f"[{subject}] {title} : {primary}")
            total_videos += 1
            u = primary.lower()
            if "m3u8" in u or "playlist" in u or "hls" in u:
                total_m3u8 += 1
            elif "youtube" in u:
                total_youtube += 1
            else:
                total_mp4 += 1
        elif normalized.get("mp4Recordings"):
            for m in normalized.get("mp4Recordings"):
                lines.append(f"[{subject}] {title} : {m}")
                total_videos += 1
                total_mp4 += 1

        for p in normalized.get("classPdf", []):
            lines.append(f"[{subject}] {title} : {p}")
            total_pdfs += 1

    course_level_pdfs = find_pdf_from_active(course_id, batches)
    if isinstance(course_level_pdfs, str):
        if course_level_pdfs and course_level_pdfs.lower() != "no pdf":
            course_level_pdfs = [u.strip() for u in re.split(r"[\n,;]+", course_level_pdfs) if u.strip()]
        else:
            course_level_pdfs = []

    if isinstance(course_level_pdfs, list) and course_level_pdfs:
        subj = course_title or "Course"
        for p in course_level_pdfs:
            lines.append(f"[{subj}] {subj} : {p}")
            total_pdfs += 1

    txt_content = "\n".join(lines)
    summary_text = (
        f"📊 Export Summary:\n"
        f"🔗 Total Links: {len(lines)}\n"
        f"🎬 Videos: {total_videos}\n"
        f"📄 PDFs: {total_pdfs}"
    )
    txt_content += "\n\n" + summary_text

    summary_dict = {
        "total_links": len(lines),
        "total_videos": total_videos,
        "total_mp4": total_mp4,
        "total_m3u8": total_m3u8,
        "total_youtube": total_youtube,
        "total_pdfs": total_pdfs,
        "summary_text": summary_text
    }

    return True, txt_content, summary_dict

# ---------------- PAGINATION KEYBOARD ----------------
def build_batch_keyboard(page=0):
    batches = get_active_batches()
    if not batches:
        return None, 0

    total = len(batches)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_batches = batches[start:end]

    keyboard = []
    for batch in page_batches:
        name = (batch.get("title") or batch.get("name") or "Untitled").strip()
        bid = str(batch.get("id") or batch.get("_id") or "")
        if not bid:
            continue
        keyboard.append([InlineKeyboardButton(name, callback_data=f"sw_batch:{bid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"sw_page:{page-1}"))
    nav.append(InlineKeyboardButton(f"📌 Page {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"sw_page:{page+1}"))

    keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="sw_back")])
    return InlineKeyboardMarkup(keyboard), total

# ---------------- HANDLERS ----------------
async def selection_way_handler(app: Client, message: Message):
    markup, total = build_batch_keyboard(page=0)
    if not markup:
        await message.reply_text("❌ Could not fetch batches. Try again later.")
        return

    await message.reply_text(
        "🎯 <b>Select a batch to extract:</b>",
        reply_markup=markup,
        parse_mode=ParseMode.HTML
    )

@app.on_callback_query(filters.regex(r"^sw_page:(\d+)$"))
async def handle_sw_pagination(app: Client, query: CallbackQuery):
    page = int(query.data.split(":")[1])
    markup, total = build_batch_keyboard(page=page)
    if not markup:
        await query.answer("Failed to load batches.", show_alert=True)
        return

    await query.message.edit_text(
        "🎯 <b>Select a batch to extract:</b>",
        reply_markup=markup,
        parse_mode=ParseMode.HTML
    )

@app.on_callback_query(filters.regex(r"^sw_batch:(.+)$"))
async def handle_sw_batch_select(app: Client, query: CallbackQuery):
    batch_id = query.data.split(":", 1)[1]
    user = query.from_user
    user_mention = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

    # Fetch name from live data
    batches = get_active_batches()
    batch_name = "Course"
    for b in batches:
        if str(b.get("id")) == batch_id or str(b.get("_id")) == batch_id:
            batch_name = b.get("title") or b.get("name") or "Course"
            break

    await query.message.edit_text("⏳ Fetching course data...")

    ok, txt, summary = build_txt_for_course(batch_id, course_title=batch_name)
    if not ok:
        await query.message.edit_text(f"❌ Failed to extract batch: {batch_name}")
        return

    try:
        # ✅ FINAL FILENAME LOGIC: Only replace INVALID characters, keep space, -, _ as-is
        safe_title = re.sub(r'[<>:"/\\|?*#%&{}[\]~$@!+=\']', '_', batch_name)
        safe_title = re.sub(r'_+', '_', safe_title)  # Optional: clean repeated underscores
        safe_title = safe_title.strip(' _')
        if not safe_title:
            safe_title = "SelectionWay_Batch"
        tmp_file_name = f"{safe_title}.txt"
        tmp_path = os.path.join(tempfile.gettempdir(), tmp_file_name)

        with open(tmp_path, "w", encoding="utf-8") as tf:
            tf.write(txt)

        # ✅ Styled caption with user mention
        caption = (
            f"࿇ ══━━ 🌟 ━━══ ࿇\n\n"
            f"🌀 **Aᴘᴘ Nᴀᴍᴇ** : Selection Way\n"
            f"👤 **Eˣᵗʳᵃᶜᵗᵉᵈ Bʸ** : {user_mention}\n"
            f"============================\n\n"
            f"🎯 **Bᴀᴛᴄʜ Nᴀᴍᴇ** : `{batch_name}`\n"
            f"<blockquote>🎬 : {summary['total_videos']} | 📁 : {summary['total_pdfs']}</blockquote>\n\n"
            f"🌐 **Jᴏɪɴ Us** : {join}\n"
            f"❄️ **Dᴀᴛᴇ** : {time_new}"
        )

        await query.message.reply_document(
            document=tmp_path,
            caption=caption,
            parse_mode=ParseMode.HTML
        )
        await query.message.delete()
    except Exception as e:
        logging.exception("File send error")
        await query.message.edit_text("❌ Error sending file.")
    finally:
        try:
            if Path(tmp_path).exists():
                os.remove(tmp_path)
        except:
            pass

# Back button handler
@app.on_callback_query(filters.regex("^sw_back$"))
async def handle_sw_back(app: Client, query: CallbackQuery):
    from Extractor.core import script
    reply_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 CʟᴀssPʟᴜs 🎯", callback_data="cpwp"),
            InlineKeyboardButton("🚀 Sᴇʟᴇᴄᴛɪᴏɴ Wᴀʏ", callback_data="selection_way")
        ],
        [
            InlineKeyboardButton("𝐁 𝐀 𝐂 𝐊", callback_data="modes_")
        ]
    ])
    await query.message.edit_text(
        script.CUSTOM_TXT,
        reply_markup=reply_markup
    )

# No-op handler
@app.on_callback_query(filters.regex("^noop$"))
async def handle_noop(app: Client, query: CallbackQuery):
    await query.answer()
