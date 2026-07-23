import os
import sys
import time
import requests
from dotenv import load_dotenv
from scraper import load_existing_jobs, fetch_job_description
from ai_generator import generate_job_application, load_resume

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_message(chat_id: str, text: str, parse_mode: str = "HTML"):
    """Sends a message to a specific Telegram chat ID."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, json=payload, timeout=15)
        res.raise_for_status()
    except Exception as e:
        # Fallback to plain text if HTML parsing fails
        if parse_mode == "HTML":
            payload["parse_mode"] = None
            try:
                requests.post(url, json=payload, timeout=15)
            except Exception as e2:
                print(f"Failed to send Telegram message: {e2}")
        else:
            print(f"Failed to send Telegram message: {e}")

def handle_apply(chat_id: str, job_id_str: str):
    """Fetches job description and generates application cover letter."""
    try:
        job_id = int(job_id_str)
    except ValueError:
        send_message(chat_id, "⚠️ Invalid job number. Please send a valid number like <code>1</code> or <code>/apply 1</code>.")
        return

    jobs = load_existing_jobs()
    selected_job = next((j for j in jobs if j.get("id") == job_id), None)

    if not selected_job:
        # Try 1-based index if id key is not found
        if 1 <= job_id <= len(jobs):
            selected_job = jobs[job_id - 1]

    if not selected_job:
        send_message(chat_id, f"❌ Job <b>#{job_id}</b> not found in saved jobs list. Use <code>/jobs</code> to view active jobs.")
        return

    job_title = selected_job['title']
    job_url = selected_job['link']

    send_message(chat_id, f"⏳ <b>Fetching details & generating application for Job #{job_id}:</b>\n<i>{job_title}</i>...")

    # Step 1: Scrape job description
    description = fetch_job_description(job_url)

    # Step 2: Generate application using AI engine
    application_msg = generate_job_application(job_title, description, job_url)

    response = (
        f"✍️ <b>Generated Application Message for Job #{job_id}</b>\n"
        f"<b>Role:</b> {job_title}\n"
        f"<b>Link:</b> {job_url}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n\n"
        f"{application_msg}"
    )

    send_message(chat_id, response)

def handle_list_jobs(chat_id: str):
    """Lists saved jobs with their IDs."""
    jobs = load_existing_jobs()
    if not jobs:
        send_message(chat_id, "📭 No jobs currently saved. Run scraper.py to find jobs!")
        return

    msg = "📋 <b>Recent Saved Jobs</b>\n\n"
    # Show last 10 jobs
    for job in jobs[-10:]:
        jid = job.get('id', '?')
        msg += f"<b>#{jid}</b> - <a href='{job['link']}'>{job['title']}</a>\n"

    msg += "\n💡 <i>Reply with a number (e.g. <b>1</b>) or <b>/apply 1</b> to generate an application cover letter!</i>"
    send_message(chat_id, msg)

def handle_resume(chat_id: str):
    """Displays current resume profile context."""
    resume_text = load_resume()
    msg = f"📄 <b>Current Resume Profile Context (resume.txt):</b>\n\n<code>{resume_text}</code>\n\n💡 <i>Edit <b>resume.txt</b> in your project folder to update your skills & background.</i>"
    send_message(chat_id, msg)

def handle_help(chat_id: str):
    msg = (
        "🤖 <b>Job Application Bot Commands</b>\n\n"
        "• <b>Send any Job Number (e.g. 1)</b> or <code>/apply 1</code>: Generate AI job application message\n"
        "• <code>/jobs</code> or <code>/list</code>: List saved jobs & numbers\n"
        "• <code>/resume</code>: View saved resume profile details\n"
        "• <code>/help</code>: Show this help menu\n"
    )
    send_message(chat_id, msg)

def process_message(message: dict):
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "").strip()

    if not text:
        return

    print(f"Received message from chat {chat_id}: {text}")

    # Optionally enforce chat ID restriction if TELEGRAM_CHAT_ID is set
    if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
        print(f"Ignoring message from unauthorized chat_id: {chat_id}")
        return

    if text.startswith("/start") or text.startswith("/help"):
        handle_help(chat_id)
    elif text.startswith("/jobs") or text.startswith("/list"):
        handle_list_jobs(chat_id)
    elif text.startswith("/resume"):
        handle_resume(chat_id)
    elif text.startswith("/apply"):
        parts = text.split()
        if len(parts) > 1:
            handle_apply(chat_id, parts[1])
        else:
            send_message(chat_id, "Please specify a job number, e.g. <code>/apply 1</code>")
    elif text.isdigit():
        handle_apply(chat_id, text)
    else:
        # Check if user sent something like "apply 1" or "#1"
        cleaned = text.replace("#", "").replace("apply", "").strip()
        if cleaned.isdigit():
            handle_apply(chat_id, cleaned)
        else:
            send_message(chat_id, "Unrecognized command. Send a job number (e.g., <code>1</code>) or <code>/jobs</code> to see available jobs.")

def start_bot():
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN is missing in environment variables.")
        sys.exit(1)

    print("🤖 Telegram Job Bot Listener active. Waiting for messages...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    offset = 0

    while True:
        try:
            params = {"offset": offset, "timeout": 20}
            response = requests.get(url, params=params, timeout=25)
            response.raise_for_status()

            data = response.json()
            if data.get("ok"):
                for result in data.get("result", []):
                    offset = result["update_id"] + 1
                    if "message" in result:
                        process_message(result["message"])
        except requests.exceptions.Timeout:
            continue
        except requests.exceptions.RequestException as e:
            print(f"Network error in bot loop: {e}")
            time.sleep(5)
        except Exception as e:
            print(f"Unexpected error in bot loop: {e}")
            time.sleep(3)

if __name__ == "__main__":
    start_bot()
