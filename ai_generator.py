import os
from dotenv import load_dotenv

load_dotenv()

RESUME_FILE = 'resume.txt'

def load_resume() -> str:
    if os.path.exists(RESUME_FILE):
        try:
            with open(RESUME_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception as e:
            print(f"Warning: Could not read {RESUME_FILE}: {e}")
    return "Full-Stack Web Developer skilled in Python, JavaScript, React, Node.js, and web application development."

def generate_job_preview(job_title: str, job_description: str) -> str:
    """
    Generates a concise preview of the job description (requirements, experience, key tech) using Gemini AI.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    
    prompt = f"""
You are an expert technical recruiter. Analyze the following job description and provide a highly concise, scannable bullet-point summary.

Job Title: {job_title}
Job Description:
{job_description}

Please summarize using ONLY these 5 sections (keep bullet points very short, maximum 1-2 lines each):
- 💰 <b>Salary & Compensation</b>: (Extract wage/salary if mentioned)
- ⏱️ <b>Schedule & Hours</b>: (e.g., Full-time, Part-time, 40 hrs/week, or specific timezones)
- 🛠️ <b>Tech Stack & Tools</b>:
- 🎓 <b>Experience & Requirements</b>: (Must include required years of experience if specified)
- 📋 <b>Key Responsibilities</b>:

FORMATTING RULES:
- Use the • character for all bullet points.
- Use <b>text</b> for bold text.
- DO NOT use markdown asterisks (*) anywhere in your response.
"""

    if not api_key or api_key == "your_gemini_api_key_here":
        # Fallback if no API key is supplied
        preview = job_description[:500] + "..." if len(job_description) > 500 else job_description
        return (
            f"⚠️ <b>GEMINI_API_KEY Not Configured</b>\n"
            f"Set <code>GEMINI_API_KEY</code> in your <code>.env</code> file for AI summarization.\n\n"
            f"<b>Raw Preview:</b>\n<i>{preview}</i>"
        )

    try:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            return response.text
        except ImportError:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(prompt)
            return response.text
    except Exception as e:
        print(f"Error generating AI preview: {e}")
        return f"❌ <b>Error generating preview with Gemini AI:</b>\n<code>{str(e)}</code>"


def generate_job_application(job_title: str, job_description: str, job_url: str = "") -> str:
    """
    Generates a tailored job application message / cover letter using Google Gemini API.
    Fallback to formatted draft if GEMINI_API_KEY is not set.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    resume_text = load_resume()

    prompt = f"""
You are an expert career counselor and professional resume/application writer.
Write a concise, compelling, and professional job application message (or cover letter) for a applicant applying to the job below.

Applicant Profile & Resume Context:
{resume_text}

Job Information:
- Job Title: {job_title}
- Job URL: {job_url}
- Job Description & Requirements:
{job_description}

Instructions:
1. Write a highly professional and polished job application message (cover letter).
2. DO NOT mention where the job was advertised or the platform name (e.g., never say "advertised on onlinejobs.ph").
3. Align the applicant's skills directly with the specific requirements in the job description.
4. Keep the tone enthusiastic, highly professional, and confident, avoiding generic buzzwords and informal language.
5. Keep length around 150-250 words so it's punchy and easy to read.
6. Include placeholders like [Your Name] or pull relevant info from the applicant profile if available.

FORMATTING RULES:
- If you use bold text, use HTML tags: <b>text</b>
- If you use bullet points, use the • character.
- DO NOT use markdown asterisks (*) anywhere in your response.
"""

    if not api_key or api_key == "your_gemini_api_key_here":
        return (
            f"⚠️ <b>GEMINI_API_KEY Not Configured</b>\n"
            f"Please set <code>GEMINI_API_KEY</code> in your <code>.env</code> file to enable AI generation.\n\n"
            f"📝 <b>Draft Application Template for:</b> {job_title}\n\n"
            f"Hi Hiring Manager,\n\n"
            f"I am writing to express my strong interest in the <b>{job_title}</b> position. "
            f"With experience in full-stack web development, Python, and JavaScript, "
            f"I am confident in my ability to contribute effectively to your team.\n\n"
            f"Key highlights:\n"
            f"• Hands-on development experience with modern web technologies.\n"
            f"• Strong problem-solving skills and attention to code quality.\n"
            f"• Quick learner adaptable to project requirements.\n\n"
            f"Job Link: {job_url}\n\n"
            f"Best regards,\n[Your Name]"
        )

    try:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            return response.text
        except ImportError:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(prompt)
            return response.text
    except Exception as e:
        print(f"Error generating AI application: {e}")
        return f"❌ <b>Error generating application message with Gemini AI:</b>\n<code>{str(e)}</code>"
