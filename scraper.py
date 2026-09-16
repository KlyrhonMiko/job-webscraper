import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
import requests
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()

# Philippine Standard Time (PST/PHT, UTC+8) - matching OnlineJobs.ph timestamps
PHT = timezone(timedelta(hours=8))

# Expanded skills list
SKILLS = [
    'react', 'node', 'python', 'javascript', 'typescript', 'frontend', 'backend', 
    'fullstack', 'full stack', 'full-stack', 'web', 'php', 'laravel', 'django', 'vue', 'angular',
    'software', 'developer', 'engineer', 'programmer', 'supabase', 'next.js', 'nextjs',
    'html', 'css', 'tailwind', 'aws', 'api', 'svelte', 'express'
]

# GIG / PART-TIME filter params appended to search URLs
JOB_TYPE_PARAMS = 'isFromJobsearchForm=1&gig=on&partTime=on'

EXCLUDE_KEYWORDS = [
    'senior', 'sr', 'lead', 'manager', 'director', 'head', 'principal', 
    'staff', 'mid', 'mid-level', 'intermediate', 'expert', 'experienced', 
    'supervisor', 'vp', 'architect', 'master', 'specialist', 'executive',
    'sysadmin', 'admin', 'administrator'
]

# Broad search terms and targeted technical queries
SEARCH_TARGETS = [
    {'name': 'Main Feed', 'type': 'search', 'query': None},
    {'name': 'Developer', 'type': 'search', 'query': 'developer'},
    {'name': 'Engineer', 'type': 'search', 'query': 'engineer'},
    {'name': 'Programmer', 'type': 'search', 'query': 'programmer'},
    {'name': 'React', 'type': 'search', 'query': 'react'},
    {'name': 'Full Stack', 'type': 'search', 'query': 'full stack'},
    {'name': 'Web Developer', 'type': 'search', 'query': 'web developer'},
    {'name': 'Frontend', 'type': 'search', 'query': 'frontend'},
    {'name': 'Backend', 'type': 'search', 'query': 'backend'},
    {'name': 'Software', 'type': 'search', 'query': 'software'},
    {'name': 'Category: Web Development', 'type': 'category', 'category': 'web-development'},
    {'name': 'Category: Software & API Development', 'type': 'category', 'category': 'software--api-development'},
    {'name': 'Category: Web Programming & Javascript', 'type': 'category', 'category': 'web-programming--javascript'},
]

JOBS_FILE = 'scraped_jobs.json'

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
}

def get_request_headers() -> Dict[str, str]:
    headers = dict(DEFAULT_HEADERS)
    cookie = os.getenv("ONLINEJOBS_COOKIE")
    if cookie:
        clean_cookie = cookie.replace('\n', '').replace('\r', '').strip()
        headers['Cookie'] = clean_cookie
    return headers

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
        headers = get_request_headers()
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

def check_job_date_updated(url: str, session: requests.Session) -> Optional[datetime]:
    """
    Fetches the detail page for a candidate job to inspect 'DATE UPDATED'.
    Catches jobs originally created weeks ago that an employer updated/renewed recently.
    """
    try:
        headers = get_request_headers()
        r = session.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, 'html.parser')
        for dd in soup.select('dd'):
            text = dd.get_text(' ', strip=True)
            if 'DATE UPDATED' in text:
                m = re.search(r'DATE UPDATED\s*([A-Za-z]+\s+\d+,\s+\d+)', text)
                if m:
                    dt = datetime.strptime(m.group(1), '%b %d, %Y').replace(tzinfo=PHT)
                    return dt
        return None
    except Exception:
        return None

def matches_skills(title: str) -> bool:
    title_lower = title.lower()
    for skill in SKILLS:
        skill_lower = skill.lower()
        if ' ' in skill_lower or '-' in skill_lower or '.' in skill_lower:
            if skill_lower in title_lower:
                return True
        else:
            # Word boundary regex avoids false positives (e.g., 'react' inside 'Reaction')
            if re.search(rf'\b{re.escape(skill_lower)}\b', title_lower):
                return True
    return False

def is_excluded_title(title: str) -> bool:
    title_lower = title.lower()
    for ex in EXCLUDE_KEYWORDS:
        if re.search(rf'\b{re.escape(ex.lower())}\b', title_lower):
            return True
    return False

def parse_card_date(date_element) -> Tuple[Optional[datetime], str]:
    if not date_element:
        return None, ''
    date_text = date_element.text
    match = re.search(r'Posted on (.*)', date_text)
    if not match:
        return None, ''
    date_string = match.group(1).strip()
    try:
        dt_str = date_string.replace(' ', 'T') if ' ' in date_string else date_string
        dt = datetime.fromisoformat(dt_str)
        # Assign Philippine Standard Time
        return dt.replace(tzinfo=PHT), date_string
    except ValueError:
        return None, date_string

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
        formatted_date = job.get('postedDate', '')
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
    seen_in_this_run = set()

    # Timezone-aware cutoff in Philippine Time
    # Default 24h window provides a 4x buffer for 6h cron cycles without duplicate alerts
    LOOKBACK_HOURS = int(os.getenv('LOOKBACK_HOURS', '24'))
    now_pht = datetime.now(PHT)
    cutoff_time = now_pht - timedelta(hours=LOOKBACK_HOURS)

    print(f"Starting job search across broad terms, skills, and technical categories...")
    print(f"Current PHT Time: {now_pht.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Job cutoff window ({LOOKBACK_HOURS}h): {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')}")

    session = requests.Session()
    session.headers.update(get_request_headers())

    MAX_CONSECUTIVE_DUPLICATES = 15
    MAX_CONSECUTIVE_OLD = 10
    MAX_PAGES_PER_TARGET = 5 # 30 jobs per page * 5 = 150 jobs checked per target

    for target in SEARCH_TARGETS:
        print(f"\n--- Scraping: {target['name']} ---")

        visited_urls = set()
        offset = 0
        should_continue = True
        consecutive_duplicates = 0
        consecutive_old = 0
        page_count = 0

        while should_continue and page_count < MAX_PAGES_PER_TARGET:
            page_count += 1
            if target['type'] == 'search':
                query = target['query']
                if query is None:
                    base = "https://www.onlinejobs.ph/jobseekers/jobsearch" if offset == 0 else f"https://www.onlinejobs.ph/jobseekers/jobsearch/{offset}"
                    url = f"{base}?{JOB_TYPE_PARAMS}"
                else:
                    formatted_kw = query.replace(' ', '+')
                    base = "https://www.onlinejobs.ph/jobseekers/jobsearch" if offset == 0 else f"https://www.onlinejobs.ph/jobseekers/jobsearch/{offset}"
                    url = f"{base}?jobkeyword={formatted_kw}&{JOB_TYPE_PARAMS}"
            elif target['type'] == 'category':
                cat = target['category']
                base = f"https://www.onlinejobs.ph/jobseekers/search/c/{cat}" if offset == 0 else f"https://www.onlinejobs.ph/jobseekers/search/c/{cat}/{offset}"
                url = f"{base}?{JOB_TYPE_PARAMS}"
            else:
                break

            print(f"Navigating to {url}...")

            if url in visited_urls:
                print(f"Detected redirect or duplicate page at {url}. Stopping pagination.")
                break
            visited_urls.add(url)

            try:
                r = session.get(url, timeout=12)
                r.raise_for_status()

                soup = BeautifulSoup(r.text, 'html.parser')
                job_rows = soup.select('.jobpost-cat-box')

                if not job_rows:
                    print("No jobs found on this page. Stopping pagination for this target.")
                    break

                page_has_recent_jobs = False

                for row in job_rows:
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

                    if not job_link_element or not title_text:
                        continue

                    link = job_link_element.get('href') or ''
                    full_link = link if link.startswith('http') else f"https://www.onlinejobs.ph{link}"

                    # Track duplicates against existing database
                    if full_link in existing_links:
                        consecutive_duplicates += 1
                        if consecutive_duplicates >= MAX_CONSECUTIVE_DUPLICATES:
                            print(f"Reached {MAX_CONSECUTIVE_DUPLICATES} consecutive duplicates. Stopping pagination.")
                            should_continue = False
                            break
                        continue
                    else:
                        consecutive_duplicates = 0

                    if full_link in seen_in_this_run:
                        continue

                    # Filter out excluded titles (e.g., senior, director, sysadmin)
                    if is_excluded_title(title_text):
                        continue

                    # Filter for desired skills / tech keywords
                    if not matches_skills(title_text):
                        continue

                    # Parse date from search card
                    date_element = row.select_one('p.fs-13 em')
                    posted_date, date_string = parse_card_date(date_element)

                    # Check if posted date is within the lookback window
                    is_recent = bool(posted_date and posted_date >= cutoff_time)
                    if is_recent:
                        page_has_recent_jobs = True

                    # If posted date is older or missing, inspect detail page for 'DATE UPDATED'
                    # to catch renewed / updated postings (like job 1713590).
                    # Since OnlineJobs.ph only lists the date (e.g., 'Sep 14, 2026' with no hours),
                    # we consider it recent if the update date is on or after the cutoff date.
                    if not is_recent:
                        dt_updated = check_job_date_updated(full_link, session)
                        if dt_updated:
                            # Compare calendar dates or if next day boundary falls within cutoff
                            if dt_updated.date() >= cutoff_time.date() or (dt_updated + timedelta(days=1)) >= cutoff_time:
                                is_recent = True
                                page_has_recent_jobs = True
                                posted_date = dt_updated
                                date_string = dt_updated.strftime("%Y-%m-%d %H:%M:%S")
                                print(f"Captured recently updated job: '{title_text}' (Updated: {dt_updated.strftime('%b %d, %Y')})")

                    if not is_recent:
                        consecutive_old += 1
                        if consecutive_old >= MAX_CONSECUTIVE_OLD:
                            print(f"Found {MAX_CONSECUTIVE_OLD} consecutive jobs older than cutoff. Stopping pagination.")
                            should_continue = False
                            break
                        continue
                    else:
                        consecutive_old = 0

                    seen_in_this_run.add(full_link)
                    jobs_scraped.append({
                        'title': title_text,
                        'postedDate': date_string,
                        'link': full_link
                    })

                # If an entire page has no recent jobs and we already encountered duplicates/old jobs, stop
                if not page_has_recent_jobs and offset > 0:
                    print(f"No recent jobs found on page {page_count}. Stopping pagination for this target.")
                    should_continue = False

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

    print(f"\nFound {len(jobs_scraped)} matching jobs. ({len(new_jobs)} NEW, {len(repeated_jobs)} PREVIOUSLY SCRAPED)")

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
