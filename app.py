import os
import json
import re
import threading
import requests
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_from_directory
from dotenv import load_dotenv

load_dotenv()

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_CENTER
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

app = Flask(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
GROQ_API_KEY         = os.getenv("GROQ_API_KEY", "")
WA_PHONE_NUMBER_ID   = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_ACCESS_TOKEN      = os.getenv("WA_ACCESS_TOKEN", "")
AGENT_WHATSAPP       = os.getenv("AGENT_WHATSAPP", "")
NOTION_TOKEN         = os.getenv("NOTION_TOKEN", "")
NOTION_DB_ID         = os.getenv("NOTION_DB_ID", "")
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID", "")
GOOGLE_CREDS_JSON    = os.getenv("GOOGLE_CREDS_JSON", "")
VERIFY_TOKEN         = os.getenv("VERIFY_TOKEN", "propertyiq2025")
RENDER_URL           = os.getenv("RENDER_URL", "https://propertyiq-q0ka.onrender.com")
AGENT_DASHBOARD_KEY  = os.getenv("AGENT_DASHBOARD_KEY", "alnoor2025")
# Agent creates this once at calendar.google.com → Appointment Schedules → share URL
CALENDAR_BOOKING_URL = os.getenv("CALENDAR_BOOKING_URL", "")

# ─── Persistence ──────────────────────────────────────────────────────────────
CONV_FILE  = "/tmp/propertyiq_convs.json"
_conv_lock = threading.Lock()
conversations = {}

def _save():
    """Write conversations to disk. Called after every mutation."""
    try:
        with _conv_lock:
            with open(CONV_FILE, "w", encoding="utf-8") as f:
                json.dump(conversations, f, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"[SAVE] {e}")

def _load():
    """Load conversations from disk on startup."""
    global conversations
    try:
        if os.path.exists(CONV_FILE):
            with open(CONV_FILE, "r", encoding="utf-8") as f:
                conversations = json.load(f)
            print(f"[LOAD] Restored {len(conversations)} conversations")
    except Exception as e:
        print(f"[LOAD] {e}")
        conversations = {}

# ─── Properties ───────────────────────────────────────────────────────────────
PROPERTIES = [
    {"id":"mb_2br","name":"Muscat Bay — 2BR Apartment","type":"apartment_small","price":85000,
     "size":"120 sqm","floor":"4th Floor",
     "features":["Direct sea view","Private balcony","Fully fitted kitchen","2 parking spaces","Pool and gym"],
     "status":"Available","handover":"Q2 2025","filename":"muscat_bay_2br.pdf"},
    {"id":"mb_3br","name":"Muscat Bay — 3BR Apartment","type":"apartment_large","price":130000,
     "size":"180 sqm","floor":"7th Floor",
     "features":["Panoramic sea and mountain view","Maid room","Smart home system","2 parking spaces","Rooftop pool"],
     "status":"Available","handover":"Q2 2025","filename":"muscat_bay_3br.pdf"},
    {"id":"am_2br","name":"Al Mouj — 2BR Apartment","type":"apartment_small","price":95000,
     "size":"135 sqm","floor":"3rd Floor",
     "features":["Golf course view","Italian marble kitchen","Oak flooring","1 parking space","Beach club access"],
     "status":"Last 2 units","handover":"Ready now","filename":"al_mouj_2br.pdf"},
    {"id":"am_villa","name":"Al Mouj — 4BR Villa","type":"villa","price":285000,
     "size":"380 sqm","floor":"Plot 500 sqm",
     "features":["Private swimming pool","3-car garage","Smart home","Landscaped garden","Direct beach access"],
     "status":"1 unit only","handover":"Ready now","filename":"al_mouj_villa.pdf"},
    {"id":"tw_studio","name":"The Wave — 1BR Studio","type":"studio","price":55000,
     "size":"75 sqm","floor":"2nd Floor",
     "features":["Marina view","Full furniture package option","Rental permit available","Hotel-managed option"],
     "status":"Available","handover":"Q1 2025","filename":"the_wave_studio.pdf"},
]

# ─── Utilities ────────────────────────────────────────────────────────────────
def normalize_phone(raw):
    digits = re.sub(r'\D', '', str(raw))
    if digits.startswith('00'):
        digits = digits[2:]
    return digits

def detect_language(text):
    return "arabic" if re.search(r'[\u0600-\u06FF]', text) else "english"

def get_conversation(phone):
    key = normalize_phone(phone)
    if key not in conversations:
        conversations[key] = {
            "state": "new",
            "language": "english",
            "name": None,
            "property_type": None,
            "budget": None,
            "timeline": None,
            "history": [],
            "booking_confirmed": False,
            "booking_link_sent": False,
            "source": "whatsapp",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "email": None,
        }
        _save()
    return conversations[key]

def add_to_history(phone, role, text):
    """Add a message to conversation history and persist immediately."""
    key = normalize_phone(phone)
    conv = get_conversation(key)
    conv["history"].append({
        "role": role,
        "text": text,
        "time": datetime.now().strftime("%H:%M")
    })
    _save()

# ─── Message Templates ────────────────────────────────────────────────────────
MESSAGES = {
    "english": {
        "welcome":
            "Hello! Welcome to Al Noor Properties 🏠\n\n"
            "I'm PropertyIQ, your personal property assistant. I'll help you find the "
            "perfect property in Muscat in just a few quick questions.\n\n"
            "May I know your name?",
        "ask_type":
            "Thank you, {name}! 😊\n\n"
            "What type of property are you looking for?\n\n"
            "1️⃣  Apartment — 1 to 2 bedrooms\n"
            "2️⃣  Apartment — 3 or more bedrooms\n"
            "3️⃣  Villa\n"
            "4️⃣  Studio\n\n"
            "Reply with a number or describe what you need.",
        "ask_budget":
            "What is your budget range?\n\n"
            "1️⃣  Under 60,000 OMR\n"
            "2️⃣  60,000 – 90,000 OMR\n"
            "3️⃣  90,000 – 130,000 OMR\n"
            "4️⃣  130,000 – 200,000 OMR\n"
            "5️⃣  Above 200,000 OMR\n\n"
            "Reply with a number.",
        "ask_timeline":
            "Almost done! When are you planning to make a purchase?\n\n"
            "1️⃣  Immediately\n"
            "2️⃣  Within 3 months\n"
            "3️⃣  Within 6 months\n"
            "4️⃣  Just exploring for now\n\n"
            "Reply with a number.",
        "unclear":
            "I didn't quite catch that. Could you reply with one of the numbered options above? 😊",
        "booking_signoff":
            "\n\nExcellent! 🎉 Our specialist *Ahmed Al-Balushi* will personally reach "
            "out to confirm your appointment.\n\n"
            "Ahmed Al-Balushi | Al Noor Properties | +968 9123 4567",
        "booking_link":
            "📅 *Book Your Private Viewing*\n\n"
            "Click the link below to choose a date and time that works best for you. "
            "The appointment will be confirmed instantly and added to both our calendars.\n\n"
            "{url}\n\n"
            "— Ahmed Al-Balushi | Al Noor Properties | +968 9123 4567",
        "ai_error":
            "Thank you for your message! Our specialist Ahmed Al-Balushi will be in touch "
            "with you very shortly 🙏",
    },
    "arabic": {
        "welcome":
            "أهلاً وسهلاً! مرحباً بك في عقارات النور 🏠\n\n"
            "أنا PropertyIQ، مساعدك العقاري الشخصي. راح أساعدك تلقى العقار المثالي "
            "في مسقط بأسئلة بسيطة وسريعة.\n\n"
            "ممكن أعرف اسمك؟",
        "ask_type":
            "شكراً {name}! 😊\n\n"
            "وش نوع العقار اللي تبحث عنه؟\n\n"
            "1️⃣  شقة صغيرة — غرفة أو غرفتين\n"
            "2️⃣  شقة كبيرة — ٣ غرف وأكثر\n"
            "3️⃣  فيلا\n"
            "4️⃣  استوديو\n\n"
            "ردّ برقم أو وصف اللي تبحث عنه.",
        "ask_budget":
            "وش هي ميزانيتك تقريباً؟\n\n"
            "1️⃣  أقل من ٦٠٬٠٠٠ ريال عماني\n"
            "2️⃣  ٦٠٬٠٠٠ – ٩٠٬٠٠٠ ريال عماني\n"
            "3️⃣  ٩٠٬٠٠٠ – ١٣٠٬٠٠٠ ريال عماني\n"
            "4️⃣  ١٣٠٬٠٠٠ – ٢٠٠٬٠٠٠ ريال عماني\n"
            "5️⃣  أكثر من ٢٠٠٬٠٠٠ ريال عماني\n\n"
            "ردّ برقم.",
        "ask_timeline":
            "آخر سؤال! متى تخطط تشتري العقار؟\n\n"
            "1️⃣  فوري\n"
            "2️⃣  خلال ٣ أشهر\n"
            "3️⃣  خلال ٦ أشهر\n"
            "4️⃣  بس أستكشف الحين\n\n"
            "ردّ برقم.",
        "unclear":
            "ما فهمت بشكل واضح. ممكن تردّ بأحد الخيارات المرقمة أعلاه؟ 😊",
        "booking_signoff":
            "\n\nممتاز! 🎉 متخصصنا *أحمد البلوشي* راح يتواصل معك شخصياً لتأكيد الموعد.\n\n"
            "أحمد البلوشي | عقارات النور | +968 9123 4567",
        "booking_link":
            "📅 *احجز موعد المشاهدة الخاصة*\n\n"
            "اضغط الرابط أدناه واختر اليوم والوقت اللي يناسبك. "
            "سيتم تأكيد الموعد فوراً وإضافته لتقويم الطرفين.\n\n"
            "{url}\n\n"
            "— أحمد البلوشي | عقارات النور | ‎+968 9123 4567",
        "ai_error":
            "شكراً على رسالتك! متخصصنا أحمد البلوشي راح يتواصل معك مباشرة قريباً 🙏",
    }
}

# ─── Parsers ──────────────────────────────────────────────────────────────────
def parse_property_type(text):
    t = text.lower().strip()
    if t in ["1","1️⃣"]: return "apartment_small"
    if t in ["2","2️⃣"]: return "apartment_large"
    if t in ["3","3️⃣"]: return "villa"
    if t in ["4","4️⃣"]: return "studio"
    if any(w in t for w in ["villa","فيلا","house"]): return "villa"
    if any(w in t for w in ["studio","استوديو"]): return "studio"
    if any(w in t for w in ["3 bed","3br","three","large","كبير","٣ غرف"]): return "apartment_large"
    if any(w in t for w in ["apart","flat","شقة","شقه"]): return "apartment_small"
    return None

def parse_budget(text):
    t = text.lower().strip()
    bmap = {
        "1":"Under 60000 OMR","1️⃣":"Under 60000 OMR",
        "2":"60000-90000 OMR","2️⃣":"60000-90000 OMR",
        "3":"90000-130000 OMR","3️⃣":"90000-130000 OMR",
        "4":"130000-200000 OMR","4️⃣":"130000-200000 OMR",
        "5":"Above 200000 OMR","5️⃣":"Above 200000 OMR",
    }
    if t in bmap: return bmap[t]
    if any(w in t for w in ["under 60","less than 60","أقل"]): return "Under 60000 OMR"
    if "60" in t and "90" in t: return "60000-90000 OMR"
    if "90" in t and "130" in t: return "90000-130000 OMR"
    if "130" in t and "200" in t: return "130000-200000 OMR"
    if any(w in t for w in ["above 200","over 200","more than 200","أكثر"]): return "Above 200000 OMR"
    return None

def parse_timeline(text):
    t = text.lower().strip()
    tmap = {
        "1":"Immediately","1️⃣":"Immediately",
        "2":"Within 3 months","2️⃣":"Within 3 months",
        "3":"Within 6 months","3️⃣":"Within 6 months",
        "4":"Just exploring","4️⃣":"Just exploring",
    }
    if t in tmap: return tmap[t]
    if any(w in t for w in ["now","immediate","asap","فوري","الحين"]): return "Immediately"
    if "3" in t or "three" in t: return "Within 3 months"
    if "6" in t or "six" in t: return "Within 6 months"
    if any(w in t for w in ["explor","look","أستكشف"]): return "Just exploring"
    return "Within 6 months"

def select_property(budget_str, prop_type_str):
    b = 0; bs = str(budget_str)
    if "Under" in bs: b = 55000
    elif "60000-90000" in bs: b = 75000
    elif "90000-130000" in bs: b = 110000
    elif "130000-200000" in bs: b = 165000
    elif "Above" in bs: b = 300000
    if prop_type_str == "villa" and b >= 200000:
        return next((p for p in PROPERTIES if p["id"]=="am_villa"), PROPERTIES[0])
    if prop_type_str == "studio" or b <= 60000:
        return next((p for p in PROPERTIES if p["id"]=="tw_studio"), PROPERTIES[0])
    if prop_type_str == "apartment_large" and b >= 100000:
        return next((p for p in PROPERTIES if p["id"]=="mb_3br"), PROPERTIES[0])
    if b >= 90000:
        return next((p for p in PROPERTIES if p["id"]=="am_2br"), PROPERTIES[0])
    return next((p for p in PROPERTIES if p["id"]=="mb_2br"), PROPERTIES[0])

# ─── AI / Groq ────────────────────────────────────────────────────────────────
RECOMMENDATION_PROMPT = """You are PropertyIQ, a premium real estate assistant for Al Noor Properties in Muscat, Oman.

AVAILABLE PROPERTIES:
1 | Muscat Bay 2BR | 85,000 OMR | Sea view, balcony, pool+gym | Q2 2025
2 | Muscat Bay 3BR | 130,000 OMR | Panoramic sea/mountain view, smart home | Q2 2025
3 | Al Mouj 2BR | 95,000 OMR | Golf view, marble kitchen, beach club | LAST 2 UNITS — ready now
4 | Al Mouj 4BR Villa | 285,000 OMR | Private pool, beach access | 1 UNIT ONLY — ready now
5 | The Wave Studio | 55,000 OMR | Marina view, rental permit | Q1 2025

RULES:
- Write ENTIRELY in the lead's language. Arabic = warm Gulf Khaleeji dialect.
- Keep under 120 words. WhatsApp natural paragraphs only.
- Greet by first name once.
- Recommend the single best-matching property. Mention brochure has been sent.
- End with ONE soft question inviting them to ask more or arrange a viewing.
- No sign-off line — appended separately.
"""

SALES_PROMPT = """You are PropertyIQ, an elite real estate sales closer for Al Noor Properties, Muscat, Oman. Warm, sharp, consultative and highly persuasive.

PROPERTIES:
1 | Muscat Bay 2BR | 85,000 OMR | Sea view | Q2 2025
2 | Muscat Bay 3BR | 130,000 OMR | Panoramic view, smart home | Q2 2025
3 | Al Mouj 2BR | 95,000 OMR | Beach club, golf view | LAST 2 UNITS
4 | Al Mouj Villa 4BR | 285,000 OMR | Private pool, beach | 1 LEFT
5 | The Wave Studio | 55,000 OMR | Marina view | Q1 2025

SPECIALIST: Ahmed Al-Balushi | +968 9123 4567

MISSION: Get the lead to confirm a viewing appointment.

RULES:
- Respond ENTIRELY in the lead's language. Arabic = Khaleeji Gulf dialect.
- Max 130 words. Sound like a human consultant, not a bot.
- Use lead's name naturally once per message.
- Use scarcity/urgency naturally ("only 2 units left", "viewing slots filling up").
- Handle objections confidently and empathetically.
- Always end with ONE closing question pushing toward booking.
- When the lead agrees to a viewing or says yes to an appointment: confirm warmly,
  then write [BOOKING_CONFIRMED] on its own line at the very end. Nothing after it.
- Do NOT add sign-off — appended separately.
"""

def call_groq(system_prompt, messages_list, max_tokens=350, temperature=0.7):
    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY not configured")
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "system", "content": system_prompt}] + messages_list,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        print(f"[GROQ] OK ({len(content)} chars)")
        return content
    except Exception as e:
        print(f"[GROQ] ERROR: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"[GROQ] body: {e.response.text}")
        return None

def generate_recommendation(name, budget, prop_type, timeline, language):
    user_msg = (
        f"LEAD NAME: {name}\nBUDGET: {budget}\nPROPERTY TYPE: {prop_type}\n"
        f"TIMELINE: {timeline}\nLANGUAGE: {language}\n\nWrite the WhatsApp recommendation now."
    )
    result = call_groq(RECOMMENDATION_PROMPT, [{"role": "user", "content": user_msg}], max_tokens=300)
    if result: return result
    if language == "arabic":
        return f"مرحباً {name}، لقينا لك خيار ممتاز يناسب ميزانيتك وأرسلنا لك البروشور الآن. هل تودّ تحديد موعد لزيارة خاصة؟"
    return f"Hi {name}! We've found a property that's a great match for your requirements and just sent you the brochure. Would you like to arrange a private viewing this week?"

def generate_sales_reply(conv, incoming_text):
    lang = conv.get("language", "english")
    groq_msgs = []
    profile = (
        f"[LEAD PROFILE]\nName: {conv.get('name','Unknown')}\nBudget: {conv.get('budget','Unknown')}\n"
        f"Property Type: {conv.get('property_type','Unknown')}\nTimeline: {conv.get('timeline','Unknown')}\n"
        f"Language: {lang}\nSource: {conv.get('source','whatsapp')}"
    )
    groq_msgs.append({"role": "user", "content": profile})
    groq_msgs.append({"role": "assistant", "content": "Got it. Ready to continue the sales conversation."})

    # Build conversation from history. The incoming_text is ALREADY appended to
    # conv["history"] before this function is called, so we do NOT append it again.
    # Appending it twice causes consecutive user messages which Groq rejects.
    history = conv.get("history", [])
    msgs_to_send = history[-20:] if len(history) > 20 else history

    for msg in msgs_to_send:
        role = msg["role"]
        text = msg["text"]
        if role == "user":
            # Skip if last groq_msg is already a user with same text (dedup safety)
            if groq_msgs and groq_msgs[-1]["role"] == "user" and groq_msgs[-1]["content"] == text:
                continue
            groq_msgs.append({"role": "user", "content": text})
        elif role in ["bot", "agent"]:
            if groq_msgs and groq_msgs[-1]["role"] == "assistant" and groq_msgs[-1]["content"] == text:
                continue
            groq_msgs.append({"role": "assistant", "content": text})

    # Ensure the conversation ends with the user's message (it should, since we
    # appended incoming_text to history before calling this function)
    if not groq_msgs or groq_msgs[-1]["role"] != "user":
        groq_msgs.append({"role": "user", "content": incoming_text})

    print(f"[GROQ] Sending {len(groq_msgs)} messages to Groq")
    return call_groq(SALES_PROMPT, groq_msgs, max_tokens=350, temperature=0.72)

# ─── WhatsApp Senders ─────────────────────────────────────────────────────────
def send_whatsapp_text(to_number, message_text):
    clean = normalize_phone(to_number)
    if not clean:
        print(f"[WA] Invalid phone: '{to_number}'"); return False
    if not WA_PHONE_NUMBER_ID or not WA_ACCESS_TOKEN:
        print(f"[WA DEMO] → +{clean}: {message_text[:80]}"); return True
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": clean, "type": "text",
        "text": {"preview_url": False, "body": message_text}
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        print(f"[WA] Sent → +{clean}")
        return True
    except Exception as e:
        print(f"[WA] ERROR → +{clean}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"[WA] body: {e.response.text}")
        return False

def send_whatsapp_document(to_number, pdf_url, filename):
    clean = normalize_phone(to_number)
    if not WA_PHONE_NUMBER_ID or not WA_ACCESS_TOKEN:
        print(f"[WA DEMO] Doc '{filename}' → +{clean}"); return True
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": clean, "type": "document",
        "document": {"link": pdf_url, "filename": filename}
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        print(f"[WA] Doc sent → +{clean}")
        return True
    except Exception as e:
        print(f"[WA] Doc ERROR: {e}"); return False

def _send_brochure(phone, matched_property):
    """
    Send the property brochure PDF via WhatsApp document message.
    Falls back to a rich text summary if the PDF file doesn't exist on disk
    (e.g. when ReportLab is not installed on the server).
    """
    pdf_path = os.path.join(os.path.dirname(__file__), "static", "brochures", matched_property["filename"])
    pdf_url  = f"{RENDER_URL}/static/brochures/{matched_property['filename']}"

    if os.path.exists(pdf_path):
        # PDF exists — send as WhatsApp document
        ok = send_whatsapp_document(phone, pdf_url, matched_property["name"] + ".pdf")
        if ok:
            print(f"[BROCHURE] Sent PDF → +{phone}")
            return
        print(f"[BROCHURE] PDF send failed, falling back to text summary")

    # PDF doesn't exist or send failed — send key details as a text message
    p = matched_property
    features = "\n".join(f"  • {f}" for f in p.get("features", [])[:4])
    summary = (
        f"📄 *{p['name']}*\n"
        f"💰 OMR {p['price']:,}\n"
        f"📐 {p['size']} | {p['floor']}\n"
        f"🏗 Handover: {p['handover']} | Status: {p['status']}\n\n"
        f"*Key Features:*\n{features}\n\n"
        f"📞 Ahmed Al-Balushi | Al Noor Properties | +968 9123 4567"
    )
    send_whatsapp_text(phone, summary)
    print(f"[BROCHURE] Sent text summary → +{phone}")


def send_agent_alert(conv, phone, alert_type="new"):
    if not AGENT_WHATSAPP:
        print(f"[DEMO] Agent alert ({alert_type}): {conv.get('name')} +{phone}"); return
    is_hot = any(k in str(conv.get("budget","")) for k in ["130000","200000","Above"])
    if alert_type == "booking":
        header = "🔥 HOT — BOOKING CONFIRMED" if is_hot else "✅ BOOKING CONFIRMED"
        footer = "Lead confirmed a viewing appointment.\n"
    else:
        header = "🔥 HOT LEAD" if is_hot else "🔔 NEW LEAD"
        footer = "AI rec + brochure sent. Bot is nurturing.\n"
    alert = (
        f"{header} — PropertyIQ\n\n"
        f"Name: {conv.get('name','Unknown')}\nPhone: +{phone}\n"
        f"Budget: {conv.get('budget','—')}\n"
        f"Property: {str(conv.get('property_type','')).replace('_',' ').title()}\n"
        f"Timeline: {conv.get('timeline','—')}\n"
        f"Language: {'Arabic' if conv.get('language')=='arabic' else 'English'}\n\n"
        f"{footer}"
        f"📋 Dashboard: {RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}"
    )
    send_whatsapp_text(AGENT_WHATSAPP, alert)

# ─── CRM Logging ──────────────────────────────────────────────────────────────
def log_to_sheets(conv, phone, ai_response):
    if not GSPREAD_AVAILABLE or not GOOGLE_CREDS_JSON or not SPREADSHEET_ID: return
    try:
        creds = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
        )
        sheet = gspread.authorize(creds).open_by_key(SPREADSHEET_ID).worksheet("Leads")
        hot = "YES" if any(k in str(conv.get("budget","")) for k in ["130000","200000","Above"]) else "NO"
        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            conv.get("name",""), f"+{phone}", conv.get("email",""),
            conv.get("budget",""), str(conv.get("property_type","")).replace("_"," ").title(),
            conv.get("language",""), conv.get("timeline",""), ai_response, "New", hot, ""
        ])
        print("[SHEETS] Logged")
    except Exception as e:
        print(f"[SHEETS] ERROR: {e}")

def log_to_notion(conv, phone, ai_response):
    if not NOTION_TOKEN or not NOTION_DB_ID: return
    hot = any(k in str(conv.get("budget","")) for k in ["130000","200000","Above"])
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Lead Name": {"title": [{"text": {"content": conv.get("name","Unknown")}}]},
            "Phone": {"phone_number": f"+{phone}"},
            "Budget": {"multi_select": [{"name": str(conv.get("budget","")).replace(",","")}]},
            "Property Type": {"multi_select": [{"name": str(conv.get("property_type","")).replace(",","").replace("_"," ").title()}]},
            "Language": {"multi_select": [{"name": "Arabic" if conv.get("language")=="arabic" else "English"}]},
            "Their Message": {"rich_text": [{"text": {"content": str(conv.get("timeline",""))}}]},
            "AI Response": {"rich_text": [{"text": {"content": ai_response}}]},
            "Status": {"select": {"name": "New"}},
            "Submitted At": {"date": {"start": datetime.now().isoformat()}},
            "Hot Lead": {"checkbox": hot}
        }
    }
    try:
        r = requests.post(
            "https://api.notion.com/v1/pages",
            headers={"Authorization": f"Bearer {NOTION_TOKEN}","Content-Type":"application/json","Notion-Version":"2022-06-28"},
            json=payload, timeout=15
        )
        r.raise_for_status()
        print("[NOTION] Logged")
    except Exception as e:
        print(f"[NOTION] ERROR: {e}")
        if hasattr(e,'response') and e.response is not None:
            print(f"[NOTION] body: {e.response.text}")

# ─── Brochure Generation ──────────────────────────────────────────────────────
def generate_brochures():
    if not REPORTLAB_AVAILABLE: return
    folder = os.path.join(os.path.dirname(__file__), "static", "brochures")
    os.makedirs(folder, exist_ok=True)
    GOLD = colors.HexColor("#C9A84C"); INK = colors.HexColor("#1A1612")
    MUTED = colors.HexColor("#7A6F68"); LIGHT = colors.HexColor("#FDF8EC")
    for prop in PROPERTIES:
        fp = os.path.join(folder, prop["filename"])
        if os.path.exists(fp): continue
        try:
            doc = SimpleDocTemplate(fp, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
            s = []
            lb = ParagraphStyle("lb", fontName="Helvetica-Bold", fontSize=9, textColor=GOLD, leading=14)
            bd = ParagraphStyle("bd", fontName="Helvetica", fontSize=10, textColor=MUTED, leading=16)
            ct = ParagraphStyle("ct", fontName="Helvetica", fontSize=10, textColor=MUTED, leading=16, alignment=TA_CENTER)
            hdr = Table([
                [Paragraph("AL NOOR PROPERTIES", ParagraphStyle("hl",fontName="Helvetica-Bold",fontSize=10,textColor=GOLD,leading=14,alignment=TA_CENTER))],
                [Paragraph(prop["name"], ParagraphStyle("t",fontName="Helvetica-Bold",fontSize=22,textColor=colors.white,leading=28,alignment=TA_CENTER))],
                [Paragraph("Muscat, Sultanate of Oman", ParagraphStyle("s",fontName="Helvetica",fontSize=11,textColor=colors.HexColor("#D4A017"),leading=16,alignment=TA_CENTER))],
            ], colWidths=[170*mm])
            hdr.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),INK),
                ("TOPPADDING",(0,0),(-1,0),16),("BOTTOMPADDING",(0,0),(-1,0),4),
                ("TOPPADDING",(0,1),(-1,1),4),("BOTTOMPADDING",(0,1),(-1,1),4),
                ("TOPPADDING",(0,2),(-1,2),4),("BOTTOMPADDING",(0,2),(-1,2),16),
                ("LINEBELOW",(0,0),(-1,-1),3,GOLD),
            ]))
            s += [hdr, Spacer(1,16),
                  Paragraph(f"OMR {prop['price']:,}", ParagraphStyle("pr",fontName="Helvetica-Bold",fontSize=28,textColor=GOLD,leading=34,alignment=TA_CENTER)),
                  Spacer(1,8), HRFlowable(width="100%",thickness=0.5,color=GOLD), Spacer(1,16)]
            det = Table([[Paragraph(r[0],lb),Paragraph(r[1],bd),Paragraph(r[2],lb),Paragraph(r[3],bd)] for r in [
                ["Type",prop["type"].replace("_"," ").title(),"Size",prop["size"]],
                ["Floor",prop["floor"],"Status",prop["status"]],
                ["Handover",prop["handover"],"Project",prop["name"].split("—")[0].strip()],
            ]], colWidths=[30*mm,55*mm,35*mm,50*mm])
            det.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),LIGHT),("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#E8D5A3")),
                ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
                ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ]))
            s += [det, Spacer(1,20), Paragraph("KEY FEATURES",lb), Spacer(1,8)]
            feat = Table([[Paragraph("✦",ParagraphStyle("dot",fontName="Helvetica",fontSize=10,textColor=GOLD,leading=16)),Paragraph(f,bd)] for f in prop["features"]], colWidths=[8*mm,162*mm])
            feat.setStyle(TableStyle([("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),("LEFTPADDING",(0,0),(-1,-1),0),("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
            s += [feat, Spacer(1,20), HRFlowable(width="100%",thickness=0.5,color=colors.HexColor("#E8D5A3")), Spacer(1,16),
                  Paragraph("CONTACT YOUR SPECIALIST",lb), Spacer(1,8),
                  Paragraph("Ahmed Al-Balushi  |  Senior Property Specialist",ct),
                  Paragraph("Al Noor Properties, Muscat, Oman",ct),
                  Paragraph("+968 9123 4567  |  info@alnoorproperties.om",ct)]
            doc.build(s)
            print(f"[BROCHURE] {prop['filename']}")
        except Exception as e:
            print(f"[BROCHURE] ERROR {prop['filename']}: {e}")

# ─── Core Message Processor ───────────────────────────────────────────────────
def process_message(raw_phone, incoming_text):
    """
    This runs in a background thread (called from webhook_receive).
    All state mutations are persisted immediately via _save().
    """
    phone = normalize_phone(raw_phone)
    if not phone:
        print(f"[PROCESS] Bad phone: '{raw_phone}'"); return

    conv = get_conversation(phone)
    print(f"[PROCESS] +{phone} state={conv['state']} text='{incoming_text[:60]}'")
    print(f"[PROCESS] GROQ={'SET' if GROQ_API_KEY else 'MISSING!'} WA_ID={'SET' if WA_PHONE_NUMBER_ID else 'MISSING'} WA_TOKEN={'SET' if WA_ACCESS_TOKEN else 'MISSING'}")

    # Detect Arabic on early messages
    if conv["state"] in ["new","asked_name"] and detect_language(incoming_text) == "arabic":
        conv["language"] = "arabic"

    lang = conv["language"]
    msgs = MESSAGES[lang]

    # ── Always log the incoming lead message first ────────────────────────────
    conv["history"].append({
        "role": "user",
        "text": incoming_text,
        "time": datetime.now().strftime("%H:%M")
    })
    _save()

    # ── STATE MACHINE ─────────────────────────────────────────────────────────

    if conv["state"] == "new":
        conv["state"] = "asked_name"
        reply = msgs["welcome"]
        send_whatsapp_text(phone, reply)
        conv["history"].append({"role":"bot","text":reply,"time":datetime.now().strftime("%H:%M")})
        _save(); return

    if conv["state"] == "asked_name":
        words = incoming_text.strip().split()
        conv["name"] = words[0].capitalize() if words else "Friend"
        conv["state"] = "asked_type"
        reply = msgs["ask_type"].format(name=conv["name"])
        send_whatsapp_text(phone, reply)
        conv["history"].append({"role":"bot","text":reply,"time":datetime.now().strftime("%H:%M")})
        _save(); return

    if conv["state"] == "asked_type":
        prop_type = parse_property_type(incoming_text)
        if not prop_type:
            send_whatsapp_text(phone, msgs["unclear"]); return
        conv["property_type"] = prop_type
        conv["state"] = "asked_budget"
        reply = msgs["ask_budget"]
        send_whatsapp_text(phone, reply)
        conv["history"].append({"role":"bot","text":reply,"time":datetime.now().strftime("%H:%M")})
        _save(); return

    if conv["state"] == "asked_budget":
        budget = parse_budget(incoming_text)
        if not budget:
            send_whatsapp_text(phone, msgs["unclear"]); return
        conv["budget"] = budget
        conv["state"] = "asked_timeline"
        reply = msgs["ask_timeline"]
        send_whatsapp_text(phone, reply)
        conv["history"].append({"role":"bot","text":reply,"time":datetime.now().strftime("%H:%M")})
        _save(); return

    if conv["state"] == "asked_timeline":
        conv["timeline"] = parse_timeline(incoming_text)
        rec = generate_recommendation(conv["name"], conv["budget"], conv["property_type"], conv["timeline"], lang)
        send_whatsapp_text(phone, rec)
        conv["history"].append({"role":"bot","text":rec,"time":datetime.now().strftime("%H:%M")})
        matched = select_property(conv["budget"], conv["property_type"])
        _send_brochure(phone, matched)
        log_to_sheets(conv, phone, rec)
        log_to_notion(conv, phone, rec)
        send_agent_alert(conv, phone, alert_type="new")
        conv["state"] = "ai_nurturing"
        _save(); return

    if conv["state"] == "ai_nurturing":
        ai_reply = generate_sales_reply(conv, incoming_text)
        if not ai_reply:
            fallback = msgs["ai_error"]
            send_whatsapp_text(phone, fallback)
            conv["history"].append({"role":"bot","text":fallback,"time":datetime.now().strftime("%H:%M")})
            _save(); return
        booking = "[BOOKING_CONFIRMED]" in ai_reply
        clean   = ai_reply.replace("[BOOKING_CONFIRMED]", "").strip()
        if booking:
            full = clean + msgs["booking_signoff"]
            send_whatsapp_text(phone, full)
            conv["history"].append({"role":"bot","text":full,"time":datetime.now().strftime("%H:%M")})
            conv["state"] = "handed_over"
            conv["booking_confirmed"] = True
            send_agent_alert(conv, phone, alert_type="booking")
        else:
            send_whatsapp_text(phone, clean)
            conv["history"].append({"role":"bot","text":clean,"time":datetime.now().strftime("%H:%M")})
        _save(); return

    if conv["state"] == "handed_over":
        # Lead replied after handover — alert agent and store in history (already stored above)
        if AGENT_WHATSAPP:
            send_whatsapp_text(AGENT_WHATSAPP,
                f"💬 LEAD REPLY — {conv.get('name', phone)}\n"
                f"Phone: +{phone}\nMsg: {incoming_text}\n\n"
                f"📋 {RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}")
        _save(); return

    # Safety: unknown state → reset
    print(f"[PROCESS] Unknown state '{conv['state']}' for {phone} — resetting")
    conv["state"] = "new"
    _save()

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def form():
    return render_template("form.html")

@app.route("/submit", methods=["POST"])
def submit():
    name      = request.form.get("name","").strip()
    phone_raw = request.form.get("phone","").strip()
    email     = request.form.get("email","").strip()
    budget    = request.form.get("budget","").strip()
    prop_type = request.form.get("property_type","").strip()
    language  = request.form.get("language","English").strip()
    message   = request.form.get("message","").strip()
    if not all([name, phone_raw, email, budget, prop_type]):
        return "Missing required fields", 400

    phone    = normalize_phone(phone_raw)
    lang_key = "arabic" if "arabic" in language.lower() else "english"
    pt_lower = prop_type.lower()
    prop_type_key = "apartment_small"
    if "3 or more" in pt_lower or "large" in pt_lower: prop_type_key = "apartment_large"
    elif "villa" in pt_lower:  prop_type_key = "villa"
    elif "studio" in pt_lower: prop_type_key = "studio"

    conversations[phone] = {
        "state": "ai_nurturing",
        "language": lang_key,
        "name": name.strip().split()[0].capitalize(),
        "property_type": prop_type_key,
        "budget": budget,
        "timeline": message or "Web form enquiry",
        "history": [],
        "booking_confirmed": False,
        "booking_link_sent": False,
        "source": "web_form",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "email": email,
    }
    conv = conversations[phone]
    rec = generate_recommendation(name, budget, prop_type, message or "Web form enquiry", lang_key)
    send_whatsapp_text(phone_raw, rec)
    conv["history"].append({"role":"bot","text":rec,"time":datetime.now().strftime("%H:%M")})
    matched = select_property(budget.replace(",",""), prop_type_key)
    _send_brochure(phone, matched)
    if AGENT_WHATSAPP:
        send_whatsapp_text(AGENT_WHATSAPP,
            f"🔔 NEW WEB LEAD\nName: {name}\nPhone: {phone_raw}\nEmail: {email}\n"
            f"Budget: {budget}\nProperty: {prop_type}\nLanguage: {language}\nMsg: {message or 'None'}\n\n"
            f"✅ Rec + brochure sent.\n📋 {RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}")
    log_to_sheets(conv, phone, rec)
    log_to_notion(conv, phone, rec)
    _save()
    return redirect(url_for("thank_you", name=name.split()[0]))

@app.route("/webhook", methods=["GET"])
def webhook_verify():
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == VERIFY_TOKEN:
        print("[WEBHOOK] Verified ✓")
        return request.args.get("hub.challenge"), 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook_receive():
    """
    CRITICAL: Return 200 to Meta IMMEDIATELY.
    Meta has a 20-second timeout. Any Groq call inside process_message takes
    5-30 seconds. We spawn a background thread and return 200 at once.
    """
    raw_body = request.get_data(as_text=True)
    print(f"[WEBHOOK] Received POST, body length={len(raw_body)}, content-type={request.content_type}")

    def handle():
        try:
            data = json.loads(raw_body) if raw_body else None
            if not data:
                print("[WEBHOOK THREAD] Empty body — ignored")
                return
            print(f"[WEBHOOK THREAD] Parsed OK, keys={list(data.keys())}")
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    print(f"[WEBHOOK THREAD] Change value keys: {list(value.keys())}")
                    if "statuses" in value and "messages" not in value:
                        print("[WEBHOOK THREAD] Status-only update, skipping")
                        continue
                    msgs_in = value.get("messages", [])
                    print(f"[WEBHOOK THREAD] {len(msgs_in)} message(s) in payload")
                    for msg in msgs_in:
                        msg_type = msg.get("type")
                        print(f"[WEBHOOK THREAD] msg type={msg_type} from={msg.get('from')}")
                        if msg_type != "text":
                            print(f"[WEBHOOK THREAD] Non-text type '{msg_type}', skipping")
                            continue
                        raw_phone = msg.get("from", "").strip()
                        text = (msg.get("text") or {}).get("body", "").strip()
                        if raw_phone and text:
                            print(f"[WEBHOOK THREAD] Processing +{raw_phone}: '{text[:60]}'")
                            process_message(raw_phone, text)
                        else:
                            print(f"[WEBHOOK THREAD] Missing phone or text, skipping")
        except Exception as e:
            import traceback
            print(f"[WEBHOOK THREAD] ERROR: {e}")
            traceback.print_exc()

    threading.Thread(target=handle, daemon=True).start()
    return "OK", 200  # ← returned immediately, before Groq is ever called

@app.route("/agent")
def agent_dashboard():
    if request.args.get("key","") != AGENT_DASHBOARD_KEY:
        return "Access denied", 403
    return render_template("agent.html", conversations=conversations, key=AGENT_DASHBOARD_KEY,
                           calendar_url=CALENDAR_BOOKING_URL)

@app.route("/agent/send", methods=["POST"])
def agent_send():
    if request.form.get("key","") != AGENT_DASHBOARD_KEY:
        return jsonify({"success":False}), 403
    phone_raw = request.form.get("phone","").strip()
    message   = request.form.get("message","").strip()
    if not phone_raw or not message:
        return jsonify({"success":False}), 400
    success = send_whatsapp_text(phone_raw, message)
    phone = normalize_phone(phone_raw)
    conv  = get_conversation(phone)
    conv["history"].append({"role":"agent","text":message,"time":datetime.now().strftime("%H:%M")})
    conv["state"] = "handed_over"
    _save()
    return jsonify({"success": success})

@app.route("/agent/book", methods=["POST"])
def agent_book():
    """
    Agent clicks 'Send Booking Link' in dashboard.
    Bot sends the Google Calendar appointment URL to the lead via WhatsApp.
    The lead clicks it, picks a slot, and Google Calendar handles everything.
    """
    if request.form.get("key","") != AGENT_DASHBOARD_KEY:
        return jsonify({"success":False,"error":"forbidden"}), 403
    phone_raw = request.form.get("phone","").strip()
    if not phone_raw:
        return jsonify({"success":False,"error":"phone required"}), 400

    if not CALENDAR_BOOKING_URL:
        return jsonify({
            "success": False,
            "error": "CALENDAR_BOOKING_URL not configured. Set it in Render environment variables."
        }), 400

    phone = normalize_phone(phone_raw)
    conv  = get_conversation(phone)
    lang  = conv.get("language","english")
    msgs  = MESSAGES[lang]

    booking_msg = msgs["booking_link"].format(url=CALENDAR_BOOKING_URL)
    success = send_whatsapp_text(phone_raw, booking_msg)
    conv["history"].append({"role":"bot","text":booking_msg,"time":datetime.now().strftime("%H:%M")})
    conv["booking_link_sent"] = True
    _save()
    return jsonify({"success": success, "message": "Booking link sent to lead"})

@app.route("/static/brochures/<filename>")
def serve_brochure(filename):
    return send_from_directory(os.path.join(os.path.dirname(__file__),"static","brochures"), filename)

@app.route("/thanks")
def thank_you():
    return render_template("thanks.html", name=request.args.get("name",""))

@app.route("/health")
def health():
    return jsonify({
        "status": "live",
        "conversations": len(conversations),
        "booked": sum(1 for c in conversations.values() if c.get("booking_confirmed")),
        "nurturing": sum(1 for c in conversations.values() if c.get("state")=="ai_nurturing"),
        "calendar_configured": bool(CALENDAR_BOOKING_URL),
    }), 200

@app.route("/api/conversations")
def api_conversations():
    if request.args.get("key","") != AGENT_DASHBOARD_KEY:
        return jsonify({"error":"forbidden"}), 403
    phone = request.args.get("phone")
    if phone:
        key = normalize_phone(phone)
        conv = conversations.get(key)
        if not conv: return jsonify({"error":"not found"}), 404
        return jsonify({
            "history":          conv.get("history",[]),
            "state":            conv.get("state"),
            "booking_confirmed":conv.get("booking_confirmed",False),
            "booking_link_sent":conv.get("booking_link_sent",False),
        })
    return jsonify(conversations)

@app.route("/debug")
def debug():
    if request.args.get("key","") != AGENT_DASHBOARD_KEY:
        return "Access denied", 403
    lines = [
        f"GROQ_API_KEY: {'✅ SET ('+GROQ_API_KEY[:8]+'...)' if GROQ_API_KEY else '❌ MISSING'}",
        f"WA_PHONE_NUMBER_ID: {'✅ '+WA_PHONE_NUMBER_ID if WA_PHONE_NUMBER_ID else '❌ MISSING'}",
        f"WA_ACCESS_TOKEN: {'✅ SET ('+WA_ACCESS_TOKEN[:12]+'...)' if WA_ACCESS_TOKEN else '❌ MISSING'}",
        f"AGENT_WHATSAPP: {'✅ '+AGENT_WHATSAPP if AGENT_WHATSAPP else '⚠️ not set'}",
        f"CALENDAR_BOOKING_URL: {'✅ '+CALENDAR_BOOKING_URL[:40]+'...' if CALENDAR_BOOKING_URL else '⚠️ not set — booking button will error'}",
        f"VERIFY_TOKEN: {VERIFY_TOKEN}",
        f"RENDER_URL: {RENDER_URL}",
        f"Conv file: {CONV_FILE} ({'exists' if os.path.exists(CONV_FILE) else 'not found'})",
        f"Active conversations: {len(conversations)}",
        "", "--- Conversations ---",
    ]
    for ph, c in conversations.items():
        lines.append(f"+{ph} | {c.get('name','?')} | state={c.get('state')} | msgs={len(c.get('history',[]))} | booked={c.get('booking_confirmed')}")
    return "<pre style='font-family:monospace;padding:2rem;background:#111;color:#0f0;line-height:1.8'>" + "\n".join(lines) + "</pre>"

@app.route("/api/test-webhook", methods=["POST"])
def test_webhook():
    """Dev helper — simulate an incoming WhatsApp message without Meta."""
    if request.args.get("key","") != AGENT_DASHBOARD_KEY:
        return jsonify({"error":"forbidden"}), 403
    data  = request.get_json(silent=True) or {}
    phone = data.get("phone","").strip()
    text  = data.get("text","").strip()
    if not phone or not text:
        return jsonify({"error":"phone and text required"}), 400
    threading.Thread(target=process_message, args=(phone, text), daemon=True).start()
    return jsonify({"ok":True, "queued":True})

# ─── Startup ──────────────────────────────────────────────────────────────────
_load()
generate_brochures()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
