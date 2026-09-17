import html
import json
import os
import re
import sys
import time
import base64
import threading
from typing import List, Dict, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from dotenv import load_dotenv
from scraper import (
    load_existing_jobs,
    fetch_job_description,
    scrape_jobs,
    format_job_card,
    get_job_keyboard,
    save_jobs
)
from ai_generator import generate_job_application, load_resume, generate_job_preview

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP server to allow deploying as a Free Web Service on platforms like Render."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("Telegram Job Bot is live and running.".encode("utf-8"))

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        pass

def start_health_check_server():
    # Render uses port 10000 by default, fallback to 10000 if PORT env var is missing
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        print(f"Health check HTTP server running on port {port}", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"HTTP server error: {e}", flush=True)

def self_ping_loop():
    """Pings self every 10 minutes if running on Render to prevent free web service from sleeping."""
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if not render_url:
        return

    print(f"Self-ping keep-alive enabled for {render_url}")
    while True:
        time.sleep(600)  # Wait 10 minutes
        try:
            res = requests.get(render_url, timeout=10)
            print(f"Keep-alive ping sent (Status: {res.status_code})")
        except Exception as e:
            print(f"Self-ping failed: {e}")

def send_message(chat_id: str, text: str, parse_mode: str = "HTML", reply_markup: Optional[Dict] = None):
    """Sends a message to a specific Telegram chat ID."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        res = requests.post(url, json=payload, timeout=15)
        res.raise_for_status()
    except Exception as e:
        if parse_mode == "HTML":
            payload["parse_mode"] = None
            try:
                requests.post(url, json=payload, timeout=15)
            except Exception as e2:
                print(f"Failed to send Telegram message: {e2}")
        else:
            print(f"Failed to send Telegram message: {e}")

def answer_callback_query(callback_query_id: str, text: Optional[str] = None, show_alert: bool = False):
    """Acknowledges an inline keyboard callback query with optional notification text."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = show_alert
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Failed to answer callback query: {e}")

def edit_message_text(chat_id: str, message_id: int, text: str, reply_markup: Optional[Dict] = None, parse_mode: str = "HTML"):
    """Edits an existing Telegram message text and inline keyboard."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        res = requests.post(url, json=payload, timeout=15)
        if not res.ok:
            payload["parse_mode"] = None
            payload["text"] = re.sub(r'<[^>]+>', '', text)
            requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"Failed to edit Telegram message: {e}")

def save_updated_jobs(jobs: List[Dict]) -> bool:
    """Saves updated jobs list locally and pushes to GitHub if GITHUB_TOKEN is available."""
    save_jobs(jobs)

    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        return True

    github_url = "https://api.github.com/repos/KlyrhonMiko/job-webscraper/contents/scraped_jobs.json"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        res = requests.get(github_url, headers=headers, timeout=10)
        if res.status_code == 200:
            file_info = res.json()
            sha = file_info['sha']

            raw_bytes = json.dumps(jobs, indent=2, ensure_ascii=False).encode('utf-8')
            content_b64 = base64.b64encode(raw_bytes).decode('utf-8')
            put_data = {
                "message": "Update job status via Telegram Bot",
                "content": content_b64,
                "sha": sha
            }
            put_res = requests.put(github_url, headers=headers, json=put_data, timeout=10)
            if put_res.status_code in (200, 201):
                return True
            else:
                print(f"Failed to update GitHub scraped_jobs.json: HTTP {put_res.status_code} - {put_res.text}")
    except Exception as e:
        print(f"Error syncing updated jobs to GitHub: {e}")
    return False

def process_callback_query(callback_query: dict):
    """Processes interactive button taps from job cards."""
    query_id = callback_query.get("id")
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))
    message_id = message.get("message_id")

    if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
        print(f"Ignoring callback from unauthorized chat_id: {chat_id}")
        answer_callback_query(query_id, "Unauthorized.")
        return

    if not data or ":" not in data:
        answer_callback_query(query_id)
        return

    action, job_id_str = data.split(":", 1)
    try:
        job_id = int(job_id_str)
    except ValueError:
        answer_callback_query(query_id, "Invalid job ID.")
        return

    jobs = get_latest_jobs()
    selected_job = next((j for j in jobs if j.get("id") == job_id), None)
    if not selected_job and 1 <= job_id <= len(jobs):
        selected_job = jobs[job_id - 1]

    if not selected_job:
        answer_callback_query(query_id, "Job not found in saved list.")
        return

    if action == "interest":
        selected_job["status"] = "interested"
        save_updated_jobs(jobs)
        answer_callback_query(query_id, "Marked as Interested.")
        new_text = format_job_card(selected_job, status="interested")
        new_kb = get_job_keyboard(selected_job, status="interested")
        edit_message_text(chat_id, message_id, new_text, reply_markup=new_kb)

    elif action == "dismiss":
        selected_job["status"] = "not_applicable"
        save_updated_jobs(jobs)
        answer_callback_query(query_id, "Marked as Not Applicable.")
        new_text = format_job_card(selected_job, status="not_applicable")
        new_kb = get_job_keyboard(selected_job, status="not_applicable")
        edit_message_text(chat_id, message_id, new_text, reply_markup=new_kb)

    elif action == "undo":
        selected_job["status"] = "new"
        save_updated_jobs(jobs)
        answer_callback_query(query_id, "Status reset.")
        new_text = format_job_card(selected_job, status="new")
        new_kb = get_job_keyboard(selected_job, status="new")
        edit_message_text(chat_id, message_id, new_text, reply_markup=new_kb)

    elif action == "preview":
        answer_callback_query(query_id, "Fetching preview...")
        handle_preview(chat_id, str(job_id))

    elif action == "apply":
        answer_callback_query(query_id, "Generating application...")
        handle_apply(chat_id, str(job_id))

def get_latest_jobs():
    """Fetches jobs from GitHub API if GITHUB_TOKEN is configured; otherwise uses local file."""
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        try:
            url = "https://api.github.com/repos/KlyrhonMiko/job-webscraper/contents/scraped_jobs.json"
            headers = {
                "Accept": "application/vnd.github.v3.raw",
                "Authorization": f"Bearer {github_token}"
            }
            res = requests.get(f"{url}?t={int(time.time())}", headers=headers, timeout=10)
            if res.status_code == 200:
                gh_jobs = res.json()
                for idx, job in enumerate(gh_jobs, 1):
                    if 'id' not in job:
                        job['id'] = idx
                    if 'status' not in job:
                        job['status'] = 'new'
                return gh_jobs
        except Exception as e:
            print(f"Failed to fetch jobs from GitHub API: {e}")
    
    # Fallback to local file
    return load_existing_jobs()

def handle_apply(chat_id: str, job_id_str: str):
    """Fetches job description and generates application cover letter."""
    try:
        job_id = int(job_id_str)
    except ValueError:
        send_message(chat_id, "Invalid job number. Please send a valid number like <code>1</code> or <code>/apply 1</code>.")
        return

    jobs = get_latest_jobs()
    selected_job = next((j for j in jobs if j.get("id") == job_id), None)

    if not selected_job:
        if 1 <= job_id <= len(jobs):
            selected_job = jobs[job_id - 1]

    if not selected_job:
        send_message(chat_id, f"Job <b>#{job_id}</b> not found in saved jobs list. Use <code>/jobs</code> to view active jobs.")
        return

    job_title = selected_job['title']
    job_url = selected_job['link']

    send_message(chat_id, f"<b>Fetching details & generating application for Job #{job_id}:</b>\n<i>{job_title}</i>...")

    description = fetch_job_description(job_url)
    application_msg = generate_job_application(job_title, description, job_url)

    response = (
        f"<b>Generated Application Message for Job #{job_id}</b>\n"
        f"<b>Role:</b> {job_title}\n"
        f"<b>Link:</b> {job_url}\n\n"
        f"{application_msg}"
    )

    send_message(chat_id, response)

def handle_preview(chat_id: str, job_id_str: str):
    """Fetches job description and generates a concise AI summary preview."""
    try:
        job_id = int(job_id_str)
    except ValueError:
        send_message(chat_id, "Invalid job number. Please send a valid number like <code>/preview 1</code>.")
        return

    jobs = get_latest_jobs()
    selected_job = next((j for j in jobs if j.get("id") == job_id), None)

    if not selected_job:
        if 1 <= job_id <= len(jobs):
            selected_job = jobs[job_id - 1]

    if not selected_job:
        send_message(chat_id, f"Job <b>#{job_id}</b> not found in saved jobs list. Use <code>/jobs</code> to view active jobs.")
        return

    job_title = selected_job['title']
    job_url = selected_job['link']

    send_message(chat_id, f"<b>Fetching description & extracting key requirements for Job #{job_id}:</b>\n<i>{job_title}</i>...")

    description = fetch_job_description(job_url)
    preview_msg = generate_job_preview(job_title, description)

    response = (
        f"<b>AI Job Preview: #{job_id}</b>\n"
        f"<b>Role:</b> {job_title}\n"
        f"<b>Link:</b> {job_url}\n\n"
        f"{preview_msg}"
    )

    send_message(chat_id, response)


def handle_list_jobs(chat_id: str):
    """Lists active and unreviewed saved jobs with their IDs."""
    jobs = get_latest_jobs()
    if not jobs:
        send_message(chat_id, "No jobs currently saved. Run /run to initiate a scrape.")
        return

    active_jobs = [j for j in jobs if j.get("status") != "not_applicable"]
    if not active_jobs:
        send_message(chat_id, "No active jobs. All jobs have been marked as Not Applicable. Use /dismissed to review them.")
        return

    msg = f"<b>Active Job Listings ({len(active_jobs)})</b>\n\n"
    for job in active_jobs[-15:]:
        jid = job.get('id', '?')
        status = job.get('status', 'new')
        status_label = " [Interested]" if status == "interested" else ""
        msg += f"<b>#{jid}</b> - <a href='{job['link']}'>{html.escape(job['title'])}</a>{status_label}\n"

    msg += "\n<i>Send a job number (e.g. 1) to generate an application, or /preview 1 for a summary.</i>"
    send_message(chat_id, msg)

def handle_interested(chat_id: str):
    """Lists jobs marked as Interested."""
    jobs = get_latest_jobs()
    interested_jobs = [j for j in jobs if j.get("status") == "interested"]
    if not interested_jobs:
        send_message(chat_id, "No jobs marked as Interested yet. Select Interested on job cards or review /jobs.")
        return

    msg = f"<b>Interested Job Listings ({len(interested_jobs)})</b>\n\n"
    for job in interested_jobs:
        jid = job.get('id', '?')
        msg += f"<b>#{jid}</b> - <a href='{job['link']}'>{html.escape(job['title'])}</a>\n"

    msg += "\n<i>Send /apply [number] to generate an application, or /preview [number] for a summary.</i>"
    send_message(chat_id, msg)

def handle_dismissed(chat_id: str):
    """Lists jobs marked as Not Applicable."""
    jobs = get_latest_jobs()
    dismissed_jobs = [j for j in jobs if j.get("status") == "not_applicable"]
    if not dismissed_jobs:
        send_message(chat_id, "No jobs marked as Not Applicable.")
        return

    msg = f"<b>Not Applicable Job Listings ({len(dismissed_jobs)})</b>\n\n"
    for job in dismissed_jobs[-15:]:
        jid = job.get('id', '?')
        msg += f"<s>#{jid} - {html.escape(job['title'])}</s>\n"

    msg += "\n<i>To restore a listing, find its original alert card and tap Undo.</i>"
    send_message(chat_id, msg)

def handle_stats(chat_id: str):
    """Displays tracking summary metrics."""
    jobs = get_latest_jobs()
    total = len(jobs)
    interested = sum(1 for j in jobs if j.get("status") == "interested")
    dismissed = sum(1 for j in jobs if j.get("status") == "not_applicable")
    unreviewed = total - interested - dismissed

    msg = (
        "<b>Job Tracking Statistics</b>\n\n"
        f"- Total Saved: {total}\n"
        f"- Unreviewed: {unreviewed}\n"
        f"- Interested: {interested}\n"
        f"- Not Applicable: {dismissed}\n"
    )
    send_message(chat_id, msg)

def handle_resume(chat_id: str):
    """Displays current resume profile context."""
    resume_text = load_resume()
    msg = f"<b>Current Resume Profile Context (resume.txt):</b>\n\n<code>{resume_text}</code>\n\n<i>Edit <b>resume.txt</b> in your project folder to update your skills & background.</i>"
    send_message(chat_id, msg)

def handle_reset(chat_id: str):
    """Erases the saved jobs list locally and on GitHub if token is provided."""
    github_token = os.getenv("GITHUB_TOKEN")
    github_url = "https://api.github.com/repos/KlyrhonMiko/job-webscraper/contents/scraped_jobs.json"
    
    github_success = False
    github_error_detail = None

    if github_token:
        try:
            headers = {
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            res = requests.get(github_url, headers=headers, timeout=10)
            
            if res.status_code == 200:
                file_info = res.json()
                sha = file_info['sha']
                
                empty_content = base64.b64encode(b"[]").decode('utf-8')
                put_data = {
                    "message": "Reset job list via Telegram Bot",
                    "content": empty_content,
                    "sha": sha
                }
                put_res = requests.put(github_url, headers=headers, json=put_data, timeout=10)
                if put_res.status_code in (200, 201):
                    github_success = True
                elif put_res.status_code == 403:
                    github_error_detail = "403 Forbidden (Check GITHUB_TOKEN has write/repo permissions)"
                else:
                    github_error_detail = f"HTTP {put_res.status_code}"
            else:
                github_error_detail = f"GET HTTP {res.status_code}"
        except Exception as e:
            github_error_detail = str(e)
            
    # Always reset local jobs file
    try:
        from scraper import save_jobs
        save_jobs([])
    except Exception as local_err:
        send_message(chat_id, f"<b>Error clearing local job list:</b>\n<code>{local_err}</code>")
        return

    # Notify user with clear status
    if github_token:
        if github_success:
            send_message(chat_id, "<b>Job list successfully reset on GitHub and locally.</b>")
        else:
            send_message(
                chat_id,
                f"<b>Local job list reset.</b>\n"
                f"<b>GitHub update failed:</b> <code>{github_error_detail}</code>\n\n"
                f"<i>Tip: Ensure your GITHUB_TOKEN has <b>repo</b> scope or <b>Contents: Read & write</b> permissions.</i>"
            )
    else:
        send_message(
            chat_id, 
            "<b>Local job list reset.</b>\n"
            "<i>GITHUB_TOKEN not set in .env, so GitHub file was not cleared.</i>"
        )

def handle_run(chat_id: str):
    """Triggers the scraper manually."""
    send_message(chat_id, "<b>Starting manual job scrape...</b>\n<i>This might take a few minutes. I will alert you with the results once finished.</i>")
    
    def run_scraper_thread():
        try:
            scrape_jobs()
            send_message(chat_id, "<b>Scrape complete.</b>")
        except Exception as e:
            send_message(chat_id, f"<b>Error running scraper:</b>\n<code>{e}</code>")
            
    threading.Thread(target=run_scraper_thread, daemon=True).start()

def handle_help(chat_id: str):
    msg = (
        "<b>Job Application Bot Commands</b>\n\n"
        "- <b>Send Job Number (e.g. 1)</b> or <code>/apply 1</code>: Generate application message\n"
        "- <code>/preview 1</code>: Generate summary of requirements and tech stack\n"
        "- <code>/jobs</code> or <code>/list</code>: List active job listings\n"
        "- <code>/interested</code>: List jobs marked as Interested\n"
        "- <code>/dismissed</code>: List jobs marked as Not Applicable\n"
        "- <code>/stats</code>: View job tracking statistics\n"
        "- <code>/resume</code>: View saved resume profile details\n"
        "- <code>/reset</code>: Erase all saved jobs\n"
        "- <code>/run</code>: Manually trigger the job scraper\n"
        "- <code>/help</code>: Show this help menu\n"
    )
    send_message(chat_id, msg)

def process_message(message: dict):
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "").strip()

    if not text:
        return

    print(f"Received message from chat {chat_id}: {text}")

    if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
        print(f"Ignoring message from unauthorized chat_id: {chat_id}")
        return

    if text.startswith("/start") or text.startswith("/help"):
        handle_help(chat_id)
    elif text.startswith("/jobs") or text.startswith("/list"):
        handle_list_jobs(chat_id)
    elif text.startswith("/interested") or text.startswith("/saved"):
        handle_interested(chat_id)
    elif text.startswith("/dismissed") or text.startswith("/ignored"):
        handle_dismissed(chat_id)
    elif text.startswith("/stats"):
        handle_stats(chat_id)
    elif text.startswith("/resume"):
        handle_resume(chat_id)
    elif text.startswith("/reset") or text.startswith("/clear"):
        handle_reset(chat_id)
    elif text.startswith("/run") or text.startswith("/scrape"):
        handle_run(chat_id)
    elif text.startswith("/preview"):
        parts = text.split()
        if len(parts) > 1:
            handle_preview(chat_id, parts[1])
        else:
            send_message(chat_id, "Please specify a job number, e.g. <code>/preview 1</code>")
    elif text.startswith("/apply"):
        parts = text.split()
        if len(parts) > 1:
            handle_apply(chat_id, parts[1])
        else:
            send_message(chat_id, "Please specify a job number, e.g. <code>/apply 1</code>")
    elif text.isdigit():
        handle_apply(chat_id, text)
    else:
        cleaned = text.replace("#", "").replace("apply", "").strip()
        if cleaned.isdigit():
            handle_apply(chat_id, cleaned)
        else:
            send_message(chat_id, "Unrecognized command. Send a job number (e.g., <code>1</code>) or <code>/jobs</code> to see available jobs.")


def start_bot():
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN is missing in environment variables.", flush=True)
        sys.exit(1)

    # Start HTTP health check server
    threading.Thread(target=start_health_check_server, daemon=True).start()

    # Start self-ping loop to keep Render free tier awake
    threading.Thread(target=self_ping_loop, daemon=True).start()

    print("Telegram Job Bot listener active. Waiting for messages...", flush=True)
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
                    if "callback_query" in result:
                        process_callback_query(result["callback_query"])
                    elif "message" in result:
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
