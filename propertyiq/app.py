import os
import json
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()
app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
GROQ_API_KEY         = os.getenv("GROQ_API_KEY")
WA_PHONE_NUMBER_ID   = os.getenv("WA_PHONE_NUMBER_ID")
WA_ACCESS_TOKEN      = os.getenv("WA_ACCESS_TOKEN")
AGENT_WHATSAPP       = os.getenv("AGENT_WHATSAPP")
NOTION_TOKEN         = os.getenv("NOTION_TOKEN")
NOTION_DB_ID         = os.getenv("NOTION_DB_ID")
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON    = os.getenv("GOOGLE_CREDS_JSON")

PROPERTY_LIST = """
PROPERTY 1 | TYPE: 2BR Apartment | PROJECT: Muscat Bay | PRICE: 85000 OMR
FEATURES: Direct sea view, private balcony, fully fitted kitchen, 2 parking, pool and gym
STATUS: Available | HANDOVER: Q2 2025

PROPERTY 2 | TYPE: 3BR Apartment | PROJECT: Muscat Bay | PRICE: 130000 OMR
FEATURES: Panoramic sea and mountain view, maid room, smart home, 2 parking, rooftop pool
STATUS: Available | HANDOVER: Q2 2025

PROPERTY 3 | TYPE: 2BR Apartment | PROJECT: Al Mouj | PRICE: 95000 OMR
FEATURES: Golf course view, Italian marble kitchen, beach club access
STATUS: Last 2 units remaining | HANDOVER: Ready now

PROPERTY 4 | TYPE: 4BR Villa | PROJECT: Al Mouj | PRICE: 285000 OMR
FEATURES: Private pool, 3-car garage, smart home, landscaped garden, direct beach access
STATUS: 1 unit only | HANDOVER: Ready now

PROPERTY 5 | TYPE: 1BR Studio | PROJECT: The Wave | PRICE: 55000 OMR
FEATURES: Marina view, full furniture package option, short-term rental permit available
STATUS: Available | HANDOVER: Q1 2025
"""

SYSTEM_PROMPT = f"""You are PropertyIQ, a premium real estate sales assistant for Al Noor Properties in Muscat, Oman.

AVAILABLE PROPERTIES:
{PROPERTY_LIST}

AGENT: Ahmed Al-Balushi | +96891234567

RULES:
1. Write ENTIRELY in the language specified in LEAD LANGUAGE. Do not mix languages.
2. If language is Arabic, write in warm Gulf Arabic Khaleeji dialect only. Never use Modern Standard Arabic.
3. Keep response under 160 words.
4. Greet the lead by their first name warmly at the very start. Do this ONCE and ONCE only. Do not repeat the greeting anywhere else in the message.
5. Recommend 1 to 2 properties that match their budget and property type.
6. For each property mention one specific standout feature.
7. Invite them to book a private viewing with Ahmed.
8. Never mention competitors or invent property details.
9. Format as a WhatsApp message — natural paragraphs, no bullet points, no HTML.
10. End with exactly this on a new line: Ahmed Al-Balushi | Al Noor Properties | +96891234567
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def generate_ai_response(name, budget, prop_type, language, message):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    user_content = (
        f"LEAD NAME: {name}\n"
        f"LEAD BUDGET: {budget}\n"
        f"LEAD PROPERTY TYPE: {prop_type}\n"
        f"LEAD LANGUAGE: {language}\n"
        f"LEAD MESSAGE: {message if message else 'No additional message provided'}\n\n"
        "Write the WhatsApp response now."
    )
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content}
        ],
        "max_tokens": 400,
        "temperature": 0.65
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Groq error: {e}")
        return "Thank you for your enquiry. Our team will be in touch shortly."


def send_whatsapp(to_number, message_text):
    clean_number = to_number.replace("+", "").replace(" ", "").replace("-", "")
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_number,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message_text
        }
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        print(f"WhatsApp sent to {clean_number}: {r.status_code}")
        return True
    except Exception as e:
        print(f"WhatsApp error to {clean_number}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"WhatsApp response: {e.response.text}")
        return False


def send_agent_alert(lead_name, budget, prop_type, language, phone, email, message, ai_response):
    alert = (
        f"🔔 NEW LEAD — PropertyIQ\n\n"
        f"Name: {lead_name}\n"
        f"Budget: {budget}\n"
        f"Interest: {prop_type}\n"
        f"Language: {language}\n"
        f"Phone: {phone}\n"
        f"Email: {email}\n\n"
        f"Their message: {message if message else 'None'}\n\n"
        f"✅ AI response sent to lead via WhatsApp.\n"
        f"Check Notion dashboard for full pipeline."
    )
    send_whatsapp(AGENT_WHATSAPP, alert)


def log_to_sheets(name, phone, email, budget, prop_type, language, message, ai_response, hot_lead):
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Leads")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [
            timestamp, name, phone, email,
            budget, prop_type, language, message,
            ai_response, "New", hot_lead, ""
        ]
        sheet.append_row(row)
        print("Lead logged to Google Sheets.")
    except Exception as e:
        print(f"Google Sheets error: {e}")


def log_to_notion(name, phone, email, budget, prop_type, language, message, ai_response, hot_lead):
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    clean_budget = str(budget).replace(",", "")
    clean_prop   = str(prop_type).replace(",", "")
    clean_lang   = str(language).replace(" / عربي", "").replace("Arabic / عربي", "Arabic").strip()
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Lead Name": {
                "title": [{"text": {"content": str(name)}}]
            },
            "Email": {
                "email": str(email)
            },
            "Phone": {
                "phone_number": str(phone)
            },
            "Budget": {
                "multi_select": [{"name": clean_budget}]
            },
            "Property Type": {
                "multi_select": [{"name": clean_prop}]
            },
            "Language": {
                "multi_select": [{"name": clean_lang}]
            },
            "Their Message": {
                "rich_text": [{"text": {"content": str(message or "")}}]
            },
            "AI Response": {
                "rich_text": [{"text": {"content": str(ai_response or "")}}]
            },
            "Status": {
                "select": {"name": "New"}
            },
            "Submitted At": {
                "date": {"start": datetime.now().isoformat()}
            },
            "Hot Lead": {
                "checkbox": hot_lead == "YES"
            }
        }
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        print("Lead logged to Notion.")
    except Exception as e:
        print(f"Notion error: {e}")
        print(f"Notion response: {r.text}")


def is_hot_lead(budget):
    hot_keywords = ["130,000", "200,000", "Above"]
    return "YES" if any(k in budget for k in hot_keywords) else "NO"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def form():
    return render_template("form.html")


@app.route("/submit", methods=["POST"])
def submit():
    name      = request.form.get("name", "").strip()
    phone     = request.form.get("phone", "").strip()
    email     = request.form.get("email", "").strip()
    budget    = request.form.get("budget", "").strip()
    prop_type = request.form.get("property_type", "").strip()
    language  = request.form.get("language", "English").strip()
    message   = request.form.get("message", "").strip()

    if not all([name, phone, email, budget, prop_type]):
        return "Missing required fields", 400

    # 1. Generate AI response
    ai_response = generate_ai_response(name, budget, prop_type, language, message)

    # 2. Determine if hot lead
    hot = is_hot_lead(budget)

    # 3. Send WhatsApp to lead — AI response only, no manual greeting
    send_whatsapp(phone, ai_response)

    # 4. Send agent alert
    send_agent_alert(name, budget, prop_type, language, phone, email, message, ai_response)

    # 5. Log to Google Sheets
    log_to_sheets(name, phone, email, budget, prop_type, language, message, ai_response, hot)

    # 6. Log to Notion
    log_to_notion(name, phone, email, budget, prop_type, language, message, ai_response, hot)

    return redirect(url_for("thank_you", name=name.split()[0]))


@app.route("/thanks")
def thank_you():
    name = request.args.get("name", "")
    return render_template("thanks.html", name=name)


@app.route("/health")
def health():
    return jsonify({"status": "PropertyIQ is live"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
