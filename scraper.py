import json
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict
import requests
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()

KEYWORDS = ['web developer', 'junior developer', 'developer', 'software engineer', 'react', 'node', 'python', 'programmer']
JOBS_FILE = 'scraped_jobs.json'

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def load_existing_jobs() -> List[Dict]:
    if not os.path.exists(JOBS_FILE):
        return []
    try:
        with open(JOBS_FILE, 'r', encoding='utf-8') as f:
            jobs = json.load(f)
            # Ensure all loaded jobs have an 'id'
            for idx, job in enumerate(jobs, 1):
                if 'id' not in job:
                    job['id'] = idx
            return jobs
    except Exception as e:
        print(f"Warning: Error reading {JOBS_FILE}: {e}")
        return []

def save_jobs(jobs: List[Dict]):
    try:
        with open(JOBS_FILE, 'w', encoding='utf-8') as f:
            json.dump(jobs, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(jobs)} total jobs to {JOBS_FILE}")
    except Exception as e:
        print(f"Error saving jobs: {e}")

def fetch_job_description(url: str) -> str:
    """Scrapes full job description text from an onlinejobs.ph job detail page."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
        }
        cookie = os.getenv("ONLINEJOBS_COOKIE")
        if cookie:
            # Sanitize the cookie: remove any accidental newlines or carriage returns from copy-pasting
            clean_cookie = cookie.replace('\n', '').replace('\r', '').strip()
            headers['Cookie'] = clean_cookie

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract all text from the body to ensure we capture the top header with Wage/Salary,
        # Hours, and Type of Work, as well as the main job description.
        if soup.body:
            text = soup.body.get_text(separator='\n', strip=True)
            # Remove excessive newlines
            text = '\n'.join([line for line in text.splitlines() if line.strip()])
            return text[:10000] # Pass up to 10000 chars to Gemini

        return soup.get_text(separator='\n', strip=True)[:10000]
    except Exception as e:
        print(f"Error fetching job description from {url}: {e}")
        return "Could not retrieve full job description."

def send_telegram_message(new_jobs: List[Dict]):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram bot token or chat ID is missing. Skipping Telegram notification.")
        return

    if not new_jobs:
        _post_to_telegram("🎯 <b>Daily Job Alert</b>\n<i>No new jobs found right now.</i>")
        return

    message = (
        f"🎯 <b>Daily Job Alert</b>\n"
        f"<i>Found {len(new_jobs)} new opportunities right now</i>\n\n"
        f"➖➖➖➖➖➖➖➖➖➖\n\n"
    )
    for i, job in enumerate(new_jobs, 1):
        job_id = job.get('id', i)
        formatted_date = job['postedDate']
        try:
            dt_str = formatted_date.replace(' ', 'T') if ' ' in formatted_date else formatted_date
            dt = datetime.fromisoformat(dt_str)
            formatted_date = dt.strftime("%B %d, %Y at %I:%M %p")
        except ValueError:
            pass

        message += f"<b>#{job_id} 💼 {job['title']}</b>\n"
        message += f"📅 <i>Posted: {formatted_date}</i>\n"
        message += f"🔗 <a href='{job['link']}'>View Application</a>\n\n"

        if len(message) > 3400:
            _post_to_telegram(message)
            message = ""

    if message:
        _post_to_telegram(message)

def _post_to_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print("Successfully sent message to Telegram.")
    except Exception as e:
        print(f"Failed to send message to Telegram: {e}")

def scrape_jobs():
    existing_jobs = load_existing_jobs()
    existing_links = {job['link'] for job in existing_jobs}

    new_jobs = []
    repeated_jobs = []
    jobs_scraped = []

    one_week_ago = datetime.now() - timedelta(days=7)
    visited_urls = set()
    offset = 0
    should_continue = True

    print(f"Starting job search for: {', '.join(KEYWORDS)}")

    while should_continue:
        url = f"https://www.onlinejobs.ph/jobseekers/jobsearch?q=" if offset == 0 else f"https://www.onlinejobs.ph/jobseekers/jobsearch/{offset}?q="
        print(f"Navigating to {url}...")

        if url in visited_urls:
            print(f"Detected redirect or duplicate page at {url}. Stopping pagination.")
            break
        visited_urls.add(url)

        try:
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'})
            r.raise_for_status()

            soup = BeautifulSoup(r.text, 'html.parser')
            job_rows = soup.select('.jobpost-cat-box')

            if not job_rows:
                print("No jobs found on this page. Stopping pagination.")
                break

            for row in job_rows:
                date_element = row.select_one('p.fs-13 em')
                posted_date = None
                date_string = ''
                if date_element:
                    date_text = date_element.text
                    match = re.search(r'Posted on (.*)', date_text)
                    if match:
                        date_string = match.group(1).strip()
                        try:
                            dt_str = date_string.replace(' ', 'T') if ' ' in date_string else date_string
                            posted_date = datetime.fromisoformat(dt_str)
                        except ValueError:
                            pass

                if posted_date and posted_date < one_week_ago:
                    print(f"Found job posted on {posted_date.isoformat()}, which is older than 1 week. Stopping pagination.")
                    should_continue = False
                    break

                link_elements = row.select('a')
                job_link_element = None
                for el in link_elements:
                    href = el.get('href')
                    if href and '/jobseekers/job/' in href:
                        job_link_element = el
                        break

                title_elem = row.select_one('h4')
                title_text = ''
                if title_elem:
                    for badge in title_elem.select('.badge'):
                        badge.extract()
                    title_text = title_elem.get_text(strip=True)

                if job_link_element and title_text:
                    link = job_link_element.get('href') or ''
                    full_link = link if link.startswith('http') else f"https://www.onlinejobs.ph{link}"

                    if full_link in existing_links:
                        print(f"Found already scraped job '{title_text}'. Stopping pagination.")
                        should_continue = False
                        break

                    title_lower = title_text.lower()
                    if 'senior' in title_lower:
                        continue

                    matches_keyword = any(k.lower() in title_lower for k in KEYWORDS)

                    if matches_keyword or not KEYWORDS:
                        jobs_scraped.append({
                            'title': title_text,
                            'postedDate': date_string,
                            'link': full_link
                        })

        except Exception as e:
            print(f"Error extracting jobs or reached end of pagination: {e}")
            break

        if should_continue:
            offset += 30

    # Deduplicate and assign IDs
    next_id = max([j.get('id', 0) for j in existing_jobs], default=0) + 1
    for job in jobs_scraped:
        if job['link'] in existing_links:
            repeated_jobs.append(job)
        else:
            job['id'] = next_id
            next_id += 1
            new_jobs.append(job)
            existing_links.add(job['link'])

    print(f"Found {len(jobs_scraped)} matching jobs. ({len(new_jobs)} NEW, {len(repeated_jobs)} PREVIOUSLY SCRAPED)")

    if new_jobs:
        print('\n--- NEW JOB RESULTS ---')
        for job in new_jobs:
            print(f"\n[#{job['id']}] [NEW] {job['title']}")
            print(f"Posted:   {job['postedDate']}")
            print(f"Link:     {job['link']}")
        print('\n-------------------\n')

        send_telegram_message(new_jobs)

        updated_jobs = existing_jobs + new_jobs
        save_jobs(updated_jobs)
    else:
        print('No new jobs to save or notify.')
        send_telegram_message([])

if __name__ == '__main__':
    try:
        scrape_jobs()
    except Exception as e:
        print(f"An error occurred during scraping: {e}")
