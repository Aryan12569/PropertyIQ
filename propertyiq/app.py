import os, json, re, threading
from datetime import datetime, timedelta
from urllib.parse import quote
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_from_directory
from dotenv import load_dotenv
import requests as http_requests

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

# ── Config ─────────────────────────────────────────────────────────────────────
GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "")
WA_PHONE_NUMBER_ID  = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_ACCESS_TOKEN     = os.getenv("WA_ACCESS_TOKEN", "")
AGENT_WHATSAPP      = os.getenv("AGENT_WHATSAPP", "")
NOTION_TOKEN        = os.getenv("NOTION_TOKEN", "")
NOTION_DB_ID        = os.getenv("NOTION_DB_ID", "")
SPREADSHEET_ID      = os.getenv("SPREADSHEET_ID", "")
GOOGLE_CREDS_JSON   = os.getenv("GOOGLE_CREDS_JSON", "")
VERIFY_TOKEN        = os.getenv("VERIFY_TOKEN", "propertyiq2025")
RENDER_URL          = os.getenv("RENDER_URL", "https://propertyiq-q0ka.onrender.com")
AGENT_DASHBOARD_KEY = os.getenv("AGENT_DASHBOARD_KEY", "alnoor2025")

# ── Persistence ────────────────────────────────────────────────────────────────
CONV_FILE = "/tmp/propertyiq_conversations.json"
conversations = {}

def save_conversations():
    try:
        with open(CONV_FILE, "w", encoding="utf-8") as f:
            json.dump(conversations, f, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"[SAVE] Error: {e}")

def load_conversations():
    global conversations
    try:
        if os.path.exists(CONV_FILE):
            with open(CONV_FILE, "r", encoding="utf-8") as f:
                conversations = json.load(f)
            print(f"[LOAD] Restored {len(conversations)} conversations")
    except Exception as e:
        print(f"[LOAD] Error: {e}")
        conversations = {}

# ── Properties ─────────────────────────────────────────────────────────────────
PROPERTIES = [
    {"id":"mb_2br","name":"Muscat Bay — 2BR Apartment","type":"apartment_small","price":85000,"size":"120 sqm","floor":"4th Floor","features":["Direct sea view","Private balcony","Fully fitted kitchen","2 parking spaces","Pool and gym"],"status":"Available","handover":"Q2 2025","filename":"muscat_bay_2br.pdf"},
    {"id":"mb_3br","name":"Muscat Bay — 3BR Apartment","type":"apartment_large","price":130000,"size":"180 sqm","floor":"7th Floor","features":["Panoramic sea and mountain view","Maid room","Smart home system","2 parking spaces","Rooftop pool"],"status":"Available","handover":"Q2 2025","filename":"muscat_bay_3br.pdf"},
    {"id":"am_2br","name":"Al Mouj — 2BR Apartment","type":"apartment_small","price":95000,"size":"135 sqm","floor":"3rd Floor","features":["Golf course view","Italian marble kitchen","Oak flooring","1 parking space","Beach club access"],"status":"Last 2 units","handover":"Ready now","filename":"al_mouj_2br.pdf"},
    {"id":"am_villa","name":"Al Mouj — 4BR Villa","type":"villa","price":285000,"size":"380 sqm","floor":"Plot 500 sqm","features":["Private swimming pool","3-car garage","Smart home","Landscaped garden","Direct beach access"],"status":"1 unit only","handover":"Ready now","filename":"al_mouj_villa.pdf"},
    {"id":"tw_studio","name":"The Wave — 1BR Studio","type":"studio","price":55000,"size":"75 sqm","floor":"2nd Floor","features":["Marina view","Full furniture package option","Rental permit available","Hotel-managed option"],"status":"Available","handover":"Q1 2025","filename":"the_wave_studio.pdf"},
]

# ── Utilities ──────────────────────────────────────────────────────────────────
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
            "booking_slots": [],
            "booked_slot": None,
            "source": "whatsapp",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "email": None,
        }
    return conversations[key]

def add_to_history(phone, role, text):
    key = normalize_phone(phone)
    conv = get_conversation(key)
    conv["history"].append({
        "role": role,
        "text": text,
        "time": datetime.now().strftime("%H:%M")
    })
    save_conversations()

# ── Booking Slots ──────────────────────────────────────────────────────────────
def get_available_slots():
    """Next 6 available slots — 3 Omani business days (Sun-Thu), 10am and 2pm each."""
    slots = []
    current = datetime.now()
    while len(slots) < 6:
        current += timedelta(days=1)
        # Oman weekend: Friday=4, Saturday=5
        if current.weekday() in [4, 5]:
            continue
        slots.append(current.replace(hour=10, minute=0, second=0, microsecond=0))
        slots.append(current.replace(hour=14, minute=0, second=0, microsecond=0))
    return slots[:6]

def format_slots_message(slots, language):
    day_names_en = ["Monday","Tuesday","Wednesday","Thursday","Sunday"]
    lines = []
    for i, slot in enumerate(slots, 1):
        day = slot.strftime("%A, %d %B")
        time_str = "10:00 AM" if slot.hour == 10 else "2:00 PM"
        lines.append(f"{i}️⃣  {day} at {time_str}")
    body = "\n".join(lines)
    if language == "arabic":
        return f"مواعيد المشاهدة المتاحة:\n\n{body}\n\nردّ برقم الموعد الذي يناسبك."
    return f"Available viewing appointments:\n\n{body}\n\nReply with the number of your preferred slot."

def generate_calendar_link(conv, phone, slot_iso):
    """Zero-API Google Calendar pre-filled link."""
    try:
        slot = datetime.fromisoformat(str(slot_iso))
    except Exception:
        slot = datetime.now() + timedelta(days=1)
    end = slot + timedelta(hours=1)
    start_str = slot.strftime("%Y%m%dT%H%M%S")
    end_str   = end.strftime("%Y%m%dT%H%M%S")
    title = quote(f"Property Viewing — {conv.get('name','Lead')}")
    details = quote(
        f"Lead: {conv.get('name','')}\n"
        f"Phone: +{phone}\n"
        f"Budget: {conv.get('budget','')}\n"
        f"Property: {str(conv.get('property_type','')).replace('_',' ').title()}\n"
        f"Timeline: {conv.get('timeline','')}"
    )
    return (
        f"https://calendar.google.com/calendar/render"
        f"?action=TEMPLATE&text={title}"
        f"&dates={start_str}/{end_str}"
        f"&details={details}"
        f"&location={quote('Al Noor Properties, Muscat Bay, Muscat, Oman')}"
    )

# ── Message Templates ──────────────────────────────────────────────────────────
MESSAGES = {
    "english": {
        "welcome":        "Hello! Welcome to Al Noor Properties 🏠\n\nI'm PropertyIQ, your personal property assistant. I'll help you find the perfect property in Muscat in just a few quick questions.\n\nMay I know your name?",
        "ask_type":       "Thank you, {name}! 😊\n\nWhat type of property are you looking for?\n\n1️⃣  Apartment — 1 to 2 bedrooms\n2️⃣  Apartment — 3 or more bedrooms\n3️⃣  Villa\n4️⃣  Studio\n\nReply with a number or describe what you need.",
        "ask_budget":     "What is your budget range?\n\n1️⃣  Under 60,000 OMR\n2️⃣  60,000 – 90,000 OMR\n3️⃣  90,000 – 130,000 OMR\n4️⃣  130,000 – 200,000 OMR\n5️⃣  Above 200,000 OMR\n\nReply with a number.",
        "ask_timeline":   "Almost done! When are you planning to make a purchase?\n\n1️⃣  Immediately\n2️⃣  Within 3 months\n3️⃣  Within 6 months\n4️⃣  Just exploring for now\n\nReply with a number.",
        "unclear":        "I didn't quite catch that. Could you reply with one of the numbered options above? 😊",
        "booking_signoff":"\n\nOur specialist Ahmed Al-Balushi will personally reach out to confirm your appointment.\n\nAhmed Al-Balushi | Al Noor Properties | +968 9123 4567",
        "booking_confirmed_lead": "Your viewing appointment is confirmed! 🎉\n\n📅 {slot_str}\n\nAhmed Al-Balushi will send you full details shortly.\n\nAhmed Al-Balushi | Al Noor Properties | +968 9123 4567",
        "ai_error":       "Thank you for your message! Our specialist Ahmed Al-Balushi will be in touch very shortly 🙏",
    },
    "arabic": {
        "welcome":        "أهلاً وسهلاً! مرحباً بك في عقارات النور 🏠\n\nأنا PropertyIQ، مساعدك العقاري الشخصي. راح أساعدك تلقى العقار المثالي في مسقط بأسئلة بسيطة وسريعة.\n\nممكن أعرف اسمك؟",
        "ask_type":       "شكراً {name}! 😊\n\nوش نوع العقار اللي تبحث عنه؟\n\n1️⃣  شقة صغيرة — غرفة أو غرفتين\n2️⃣  شقة كبيرة — ٣ غرف وأكثر\n3️⃣  فيلا\n4️⃣  استوديو\n\nردّ برقم أو وصف اللي تبحث عنه.",
        "ask_budget":     "وش هي ميزانيتك تقريباً؟\n\n1️⃣  أقل من ٦٠٬٠٠٠ ريال عماني\n2️⃣  ٦٠٬٠٠٠ – ٩٠٬٠٠٠ ريال عماني\n3️⃣  ٩٠٬٠٠٠ – ١٣٠٬٠٠٠ ريال عماني\n4️⃣  ١٣٠٬٠٠٠ – ٢٠٠٬٠٠٠ ريال عماني\n5️⃣  أكثر من ٢٠٠٬٠٠٠ ريال عماني\n\nردّ برقم.",
        "ask_timeline":   "آخر سؤال! متى تخطط تشتري العقار؟\n\n1️⃣  فوري\n2️⃣  خلال ٣ أشهر\n3️⃣  خلال ٦ أشهر\n4️⃣  بس أستكشف الحين\n\nردّ برقم.",
        "unclear":        "ما فهمت بشكل واضح. ممكن تردّ بأحد الخيارات المرقمة أعلاه؟ 😊",
        "booking_signoff":"\n\nمتخصصنا أحمد البلوشي راح يتواصل معك شخصياً لتأكيد الموعد.\n\nأحمد البلوشي | عقارات النور | ‎+968 9123 4567",
        "booking_confirmed_lead": "تم تأكيد موعد المشاهدة! 🎉\n\n📅 {slot_str}\n\nأحمد البلوشي راح يرسل لك التفاصيل الكاملة قريباً.\n\nأحمد البلوشي | عقارات النور | ‎+968 9123 4567",
        "ai_error":       "شكراً على رسالتك! متخصصنا أحمد البلوشي راح يتواصل معك مباشرة قريباً 🙏",
    }
}

# ── Parsers ────────────────────────────────────────────────────────────────────
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
        "5":"Above 200000 OMR","5️⃣":"Above 200000 OMR"
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
        "4":"Just exploring","4️⃣":"Just exploring"
    }
    if t in tmap: return tmap[t]
    if any(w in t for w in ["now","immediate","asap","فوري","الحين"]): return "Immediately"
    if "3" in t or "three" in t: return "Within 3 months"
    if "6" in t or "six" in t: return "Within 6 months"
    if any(w in t for w in ["explor","look","أستكشف"]): return "Just exploring"
    return "Within 6 months"

def parse_slot_number(text):
    """Extract slot number (1-6) from lead reply."""
    # Check emoji numbers first
    emoji_map = {"1️⃣":1,"2️⃣":2,"3️⃣":3,"4️⃣":4,"5️⃣":5,"6️⃣":6}
    for emoji, num in emoji_map.items():
        if emoji in text:
            return num
    # Check plain digits
    for char in text.strip():
        if char.isdigit():
            n = int(char)
            if 1 <= n <= 6:
                return n
    # Arabic digits
    arabic_digits = {"١":1,"٢":2,"٣":3,"٤":4,"٥":5,"٦":6}
    for char in text:
        if char in arabic_digits:
            return arabic_digits[char]
    return None

def select_property(budget_str, prop_type_str):
    bs = str(budget_str)
    b = 0
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

# ── AI / Groq ──────────────────────────────────────────────────────────────────
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
- Use scarcity/urgency naturally when relevant.
- Handle objections confidently and empathetically.
- Always end with ONE closing question pushing toward booking.
- Do NOT add sign-off — appended separately.
"""

def call_groq(system_prompt, messages_list, max_tokens=350, temperature=0.7):
    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY not set")
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
        r = http_requests.post(url, headers=headers, json=payload, timeout=30)
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
    user_msg = f"LEAD NAME: {name}\nBUDGET: {budget}\nPROPERTY TYPE: {prop_type}\nTIMELINE: {timeline}\nLANGUAGE: {language}\n\nWrite the WhatsApp recommendation now."
    result = call_groq(RECOMMENDATION_PROMPT, [{"role":"user","content":user_msg}], max_tokens=300)
    if result: return result
    if language == "arabic":
        return f"مرحباً {name}، لقينا لك خيار ممتاز يناسب ميزانيتك وأرسلنا لك البروشور. هل تودّ تحديد موعد لزيارة خاصة؟"
    return f"Hi {name}! We've found a great property matching your requirements and just sent you the brochure. Would you like to arrange a private viewing this week?"

def generate_sales_reply(conv, incoming_text):
    lang = conv.get("language","english")
    groq_msgs = []
    profile = (
        f"[LEAD PROFILE]\nName: {conv.get('name','Unknown')}\nBudget: {conv.get('budget','Unknown')}\n"
        f"Property Type: {conv.get('property_type','Unknown')}\nTimeline: {conv.get('timeline','Unknown')}\n"
        f"Language: {lang}\nSource: {conv.get('source','whatsapp')}"
    )
    groq_msgs.append({"role":"user","content":profile})
    groq_msgs.append({"role":"assistant","content":"Got it. Ready to continue the conversation."})
    history = conv.get("history",[])
    for msg in (history[-20:] if len(history) > 20 else history):
        if msg["role"] == "user":
            groq_msgs.append({"role":"user","content":msg["text"]})
        elif msg["role"] in ["bot","agent"]:
            groq_msgs.append({"role":"assistant","content":msg["text"]})
    groq_msgs.append({"role":"user","content":incoming_text})
    return call_groq(SALES_PROMPT, groq_msgs, max_tokens=350, temperature=0.72)

# ── WhatsApp Senders ───────────────────────────────────────────────────────────
def send_whatsapp_text(to_number, message_text):
    clean = normalize_phone(to_number)
    if not clean:
        print(f"[WA] Invalid phone: '{to_number}'")
        return False
    if not WA_PHONE_NUMBER_ID or not WA_ACCESS_TOKEN:
        print(f"[WA DEMO] → +{clean}: {message_text[:80]}")
        return True
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization":f"Bearer {WA_ACCESS_TOKEN}","Content-Type":"application/json"}
    payload = {"messaging_product":"whatsapp","recipient_type":"individual","to":clean,"type":"text","text":{"preview_url":False,"body":message_text}}
    try:
        r = http_requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        print(f"[WA] Sent → +{clean}")
        return True
    except Exception as e:
        print(f"[WA] ERROR → +{clean}: {e}")
        if hasattr(e,'response') and e.response is not None:
            print(f"[WA] body: {e.response.text}")
        return False

def send_whatsapp_document(to_number, pdf_url, filename):
    clean = normalize_phone(to_number)
    if not WA_PHONE_NUMBER_ID or not WA_ACCESS_TOKEN:
        print(f"[WA DEMO] Doc '{filename}' → +{clean}")
        return True
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization":f"Bearer {WA_ACCESS_TOKEN}","Content-Type":"application/json"}
    payload = {"messaging_product":"whatsapp","recipient_type":"individual","to":clean,"type":"document","document":{"link":pdf_url,"filename":filename}}
    try:
        r = http_requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        print(f"[WA] Doc sent → +{clean}")
        return True
    except Exception as e:
        print(f"[WA] Doc ERROR: {e}")
        return False

def send_agent_alert(conv, phone, alert_type="new"):
    if not AGENT_WHATSAPP: return
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
        f"Budget: {conv.get('budget','—')}\nProperty: {str(conv.get('property_type','')).replace('_',' ').title()}\n"
        f"Timeline: {conv.get('timeline','—')}\nLanguage: {'Arabic' if conv.get('language')=='arabic' else 'English'}\n\n"
        f"{footer}"
        f"📋 Dashboard: {RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}"
    )
    send_whatsapp_text(AGENT_WHATSAPP, alert)

# ── CRM Logging ────────────────────────────────────────────────────────────────
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
            conv.get("language",""), conv.get("timeline",""),
            ai_response, "New", hot, ""
        ])
        print("[SHEETS] Logged")
    except Exception as e:
        print(f"[SHEETS] ERROR: {e}")

def log_to_notion(conv, phone, ai_response):
    if not NOTION_TOKEN or not NOTION_DB_ID: return
    url = "https://api.notion.com/v1/pages"
    headers = {"Authorization":f"Bearer {NOTION_TOKEN}","Content-Type":"application/json","Notion-Version":"2022-06-28"}
    hot = any(k in str(conv.get("budget","")) for k in ["130000","200000","Above"])
    clean_budget = str(conv.get("budget","")).replace(",","")
    clean_prop   = str(conv.get("property_type","")).replace(",","").replace("_"," ").title()
    clean_lang   = "Arabic" if conv.get("language")=="arabic" else "English"
    payload = {
        "parent":{"database_id":NOTION_DB_ID},
        "properties":{
            "Lead Name":{"title":[{"text":{"content":conv.get("name","Unknown")}}]},
            "Phone":{"phone_number":f"+{phone}"},
            "Budget":{"multi_select":[{"name":clean_budget}]},
            "Property Type":{"multi_select":[{"name":clean_prop}]},
            "Language":{"multi_select":[{"name":clean_lang}]},
            "Their Message":{"rich_text":[{"text":{"content":conv.get("timeline","")}}]},
            "AI Response":{"rich_text":[{"text":{"content":ai_response}}]},
            "Status":{"select":{"name":"New"}},
            "Submitted At":{"date":{"start":datetime.now().isoformat()}},
            "Hot Lead":{"checkbox":hot}
        }
    }
    try:
        r = http_requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        print("[NOTION] Logged")
    except Exception as e:
        print(f"[NOTION] ERROR: {e}")
        if hasattr(e,'response') and e.response is not None:
            print(f"[NOTION] body: {e.response.text}")

# ── Brochure Generation ────────────────────────────────────────────────────────
def generate_brochures():
    if not REPORTLAB_AVAILABLE:
        print("[BROCHURE] ReportLab not available, skipping")
        return
    folder = os.path.join(os.path.dirname(__file__), "static", "brochures")
    os.makedirs(folder, exist_ok=True)
    GOLD  = colors.HexColor("#C9A84C")
    INK   = colors.HexColor("#1A1612")
    MUTED = colors.HexColor("#7A6F68")
    LIGHT = colors.HexColor("#FDF8EC")
    for prop in PROPERTIES:
        filepath = os.path.join(folder, prop["filename"])
        if os.path.exists(filepath): continue
        try:
            doc = SimpleDocTemplate(filepath,pagesize=A4,leftMargin=20*mm,rightMargin=20*mm,topMargin=20*mm,bottomMargin=20*mm)
            s=[]
            t_s  = ParagraphStyle("t",  fontName="Helvetica-Bold",fontSize=22,textColor=colors.white,leading=28,alignment=TA_CENTER)
            su_s = ParagraphStyle("su", fontName="Helvetica",fontSize=11,textColor=colors.HexColor("#D4A017"),leading=16,alignment=TA_CENTER)
            lb   = ParagraphStyle("lb", fontName="Helvetica-Bold",fontSize=9,textColor=GOLD,leading=14)
            bd   = ParagraphStyle("bd", fontName="Helvetica",fontSize=10,textColor=MUTED,leading=16)
            pr_s = ParagraphStyle("pr", fontName="Helvetica-Bold",fontSize=28,textColor=GOLD,leading=34,alignment=TA_CENTER)
            ct   = ParagraphStyle("ct", fontName="Helvetica",fontSize=10,textColor=MUTED,leading=16,alignment=TA_CENTER)
            hdr  = Table([
                [Paragraph("AL NOOR PROPERTIES",ParagraphStyle("hl",fontName="Helvetica-Bold",fontSize=10,textColor=GOLD,leading=14,alignment=TA_CENTER))],
                [Paragraph(prop["name"],t_s)],
                [Paragraph("Muscat, Sultanate of Oman",su_s)],
            ],colWidths=[170*mm])
            hdr.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),INK),
                ("TOPPADDING",(0,0),(-1,0),16),("BOTTOMPADDING",(0,0),(-1,0),4),
                ("TOPPADDING",(0,1),(-1,1),4),("BOTTOMPADDING",(0,1),(-1,1),4),
                ("TOPPADDING",(0,2),(-1,2),4),("BOTTOMPADDING",(0,2),(-1,2),16),
                ("LINEBELOW",(0,0),(-1,-1),3,GOLD),
            ]))
            s.append(hdr); s.append(Spacer(1,16))
            s.append(Paragraph(f"OMR {prop['price']:,}",pr_s)); s.append(Spacer(1,8))
            s.append(HRFlowable(width="100%",thickness=0.5,color=GOLD)); s.append(Spacer(1,16))
            details=[
                ["Type",prop["type"].replace("_"," ").title(),"Size",prop["size"]],
                ["Floor",prop["floor"],"Status",prop["status"]],
                ["Handover",prop["handover"],"Project",prop["name"].split("—")[0].strip()],
            ]
            det=Table([[Paragraph(r[0],lb),Paragraph(r[1],bd),Paragraph(r[2],lb),Paragraph(r[3],bd)]for r in details],colWidths=[30*mm,55*mm,35*mm,50*mm])
            det.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),LIGHT),("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#E8D5A3")),("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
            s.append(det); s.append(Spacer(1,20)); s.append(Paragraph("KEY FEATURES",lb)); s.append(Spacer(1,8))
            feat=Table([[Paragraph("✦",ParagraphStyle("dot",fontName="Helvetica",fontSize=10,textColor=GOLD,leading=16)),Paragraph(f,bd)]for f in prop["features"]],colWidths=[8*mm,162*mm])
            feat.setStyle(TableStyle([("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),("LEFTPADDING",(0,0),(-1,-1),0),("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
            s.append(feat); s.append(Spacer(1,20)); s.append(HRFlowable(width="100%",thickness=0.5,color=colors.HexColor("#E8D5A3"))); s.append(Spacer(1,16))
            s.append(Paragraph("CONTACT YOUR SPECIALIST",lb)); s.append(Spacer(1,8))
            s.append(Paragraph("Ahmed Al-Balushi  |  Senior Property Specialist",ct))
            s.append(Paragraph("Al Noor Properties, Muscat, Oman",ct))
            s.append(Paragraph("+968 9123 4567  |  info@alnoorproperties.om",ct))
            doc.build(s)
            print(f"[BROCHURE] Generated: {prop['filename']}")
        except Exception as e:
            print(f"[BROCHURE] ERROR {prop['filename']}: {e}")

# ── Core Message Processor ─────────────────────────────────────────────────────
def process_message(raw_phone, incoming_text):
    phone = normalize_phone(raw_phone)
    if not phone:
        print(f"[PROCESS] Bad phone: '{raw_phone}'"); return

    conv = get_conversation(phone)
    print(f"[PROCESS] +{phone} | state={conv['state']} | text='{incoming_text[:60]}'")

    # Language detection on early messages
    if conv["state"] in ["new","asked_name"] and detect_language(incoming_text) == "arabic":
        conv["language"] = "arabic"

    lang = conv["language"]
    msgs = MESSAGES[lang]

    # Always log lead message to history FIRST
    conv["history"].append({
        "role": "user",
        "text": incoming_text,
        "time": datetime.now().strftime("%H:%M")
    })
    save_conversations()

    # ── STATE MACHINE ──────────────────────────────────────────────────────────

    if conv["state"] == "new":
        conv["state"] = "asked_name"
        reply = msgs["welcome"]
        send_whatsapp_text(phone, reply)
        conv["history"].append({"role":"bot","text":reply,"time":datetime.now().strftime("%H:%M")})
        save_conversations()
        return

    if conv["state"] == "asked_name":
        words = incoming_text.strip().split()
        conv["name"] = words[0].capitalize() if words else "Friend"
        conv["state"] = "asked_type"
        reply = msgs["ask_type"].format(name=conv["name"])
        send_whatsapp_text(phone, reply)
        conv["history"].append({"role":"bot","text":reply,"time":datetime.now().strftime("%H:%M")})
        save_conversations()
        return

    if conv["state"] == "asked_type":
        prop_type = parse_property_type(incoming_text)
        if not prop_type:
            send_whatsapp_text(phone, msgs["unclear"]); return
        conv["property_type"] = prop_type
        conv["state"] = "asked_budget"
        reply = msgs["ask_budget"]
        send_whatsapp_text(phone, reply)
        conv["history"].append({"role":"bot","text":reply,"time":datetime.now().strftime("%H:%M")})
        save_conversations()
        return

    if conv["state"] == "asked_budget":
        budget = parse_budget(incoming_text)
        if not budget:
            send_whatsapp_text(phone, msgs["unclear"]); return
        conv["budget"] = budget
        conv["state"] = "asked_timeline"
        reply = msgs["ask_timeline"]
        send_whatsapp_text(phone, reply)
        conv["history"].append({"role":"bot","text":reply,"time":datetime.now().strftime("%H:%M")})
        save_conversations()
        return

    if conv["state"] == "asked_timeline":
        conv["timeline"] = parse_timeline(incoming_text)
        # Generate AI recommendation
        rec = generate_recommendation(conv["name"], conv["budget"], conv["property_type"], conv["timeline"], lang)
        send_whatsapp_text(phone, rec)
        conv["history"].append({"role":"bot","text":rec,"time":datetime.now().strftime("%H:%M")})
        # Send brochure
        matched = select_property(conv["budget"], conv["property_type"])
        send_whatsapp_document(phone, f"{RENDER_URL}/static/brochures/{matched['filename']}", matched["name"]+".pdf")
        # CRM + alert
        log_to_sheets(conv, phone, rec)
        log_to_notion(conv, phone, rec)
        send_agent_alert(conv, phone, alert_type="new")
        conv["state"] = "ai_nurturing"
        save_conversations()
        return

    if conv["state"] == "ai_nurturing":
        ai_reply = generate_sales_reply(conv, incoming_text)
        if not ai_reply:
            fallback = msgs["ai_error"]
            send_whatsapp_text(phone, fallback)
            conv["history"].append({"role":"bot","text":fallback,"time":datetime.now().strftime("%H:%M")})
            save_conversations()
            return
        send_whatsapp_text(phone, ai_reply)
        conv["history"].append({"role":"bot","text":ai_reply,"time":datetime.now().strftime("%H:%M")})
        save_conversations()
        return

    if conv["state"] == "booking_slots_sent":
        slot_num = parse_slot_number(incoming_text)
        slots = conv.get("booking_slots", [])
        if not slot_num or slot_num > len(slots):
            send_whatsapp_text(phone, msgs["unclear"]); return
        selected_iso = slots[slot_num - 1]
        conv["booked_slot"] = selected_iso
        conv["booking_confirmed"] = True
        conv["state"] = "handed_over"
        # Format slot for display
        try:
            slot_dt = datetime.fromisoformat(str(selected_iso))
            slot_str = slot_dt.strftime("%A, %d %B at %I:%M %p")
        except Exception:
            slot_str = str(selected_iso)
        # Confirm to lead
        confirm_msg = msgs["booking_confirmed_lead"].format(slot_str=slot_str)
        send_whatsapp_text(phone, confirm_msg)
        conv["history"].append({"role":"bot","text":confirm_msg,"time":datetime.now().strftime("%H:%M")})
        # Alert agent with Google Calendar link
        cal_link = generate_calendar_link(conv, phone, selected_iso)
        if AGENT_WHATSAPP:
            agent_booking_msg = (
                f"✅ VIEWING BOOKED — PropertyIQ\n\n"
                f"Lead: {conv.get('name','Unknown')}\n"
                f"Phone: +{phone}\n"
                f"Slot: {slot_str}\n"
                f"Budget: {conv.get('budget','—')}\n"
                f"Property: {str(conv.get('property_type','')).replace('_',' ').title()}\n\n"
                f"📅 Add to Google Calendar:\n{cal_link}\n\n"
                f"📋 Dashboard: {RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}"
            )
            send_whatsapp_text(AGENT_WHATSAPP, agent_booking_msg)
        save_conversations()
        return

    if conv["state"] == "handed_over":
        # Forward lead reply to agent, store in history (already stored above)
        if AGENT_WHATSAPP:
            send_whatsapp_text(AGENT_WHATSAPP,
                f"💬 LEAD REPLY — {conv.get('name', phone)}\n"
                f"Phone: +{phone}\n"
                f"Message: {incoming_text}\n\n"
                f"📋 {RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}"
            )
        return

    # Unknown state safety reset
    print(f"[PROCESS] Unknown state '{conv['state']}' → resetting")
    conv["state"] = "new"
    save_conversations()

# ── Routes ─────────────────────────────────────────────────────────────────────

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
        "booking_slots": [],
        "booked_slot": None,
        "source": "web_form",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "email": email,
    }
    conv = conversations[phone]

    rec = generate_recommendation(name, budget, prop_type, message or "Web form enquiry", lang_key)
    send_whatsapp_text(phone_raw, rec)
    conv["history"].append({"role":"bot","text":rec,"time":datetime.now().strftime("%H:%M")})
    matched = select_property(budget.replace(",",""), prop_type_key)
    send_whatsapp_document(phone_raw, f"{RENDER_URL}/static/brochures/{matched['filename']}", matched["name"]+".pdf")
    if AGENT_WHATSAPP:
        send_whatsapp_text(AGENT_WHATSAPP,
            f"🔔 NEW WEB LEAD\nName: {name}\nPhone: {phone_raw}\nEmail: {email}\n"
            f"Budget: {budget}\nProperty: {prop_type}\nLanguage: {language}\nMsg: {message or 'None'}\n\n"
            f"✅ Rec + brochure sent. Bot nurturing.\n📋 {RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}"
        )
    log_to_sheets(conv, phone, rec)
    log_to_notion(conv, phone, rec)
    save_conversations()
    return redirect(url_for("thank_you", name=name.split()[0]))

@app.route("/webhook", methods=["GET"])
def webhook_verify():
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == VERIFY_TOKEN:
        print("[WEBHOOK] Verified ✓")
        return request.args.get("hub.challenge"), 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook_receive():
    # ── Return 200 IMMEDIATELY — Meta has a 5-second timeout ──────────────────
    raw_body = request.get_data(as_text=True)

    def handle_in_background():
        try:
            data = json.loads(raw_body) if raw_body else None
            if not data: return
            for entry in data.get("entry",[]):
                for change in entry.get("changes",[]):
                    value = change.get("value",{})
                    if "statuses" in value and "messages" not in value: continue
                    for msg in value.get("messages",[]):
                        if msg.get("type") != "text": continue
                        raw_phone = msg.get("from","").strip()
                        text = (msg.get("text") or {}).get("body","").strip()
                        if raw_phone and text:
                            print(f"[WEBHOOK] +{raw_phone}: {text[:60]}")
                            process_message(raw_phone, text)
        except Exception as e:
            print(f"[WEBHOOK THREAD] ERROR: {e}")
            import traceback; traceback.print_exc()

    thread = threading.Thread(target=handle_in_background, daemon=True)
    thread.start()
    return "OK", 200

@app.route("/agent")
def agent_dashboard():
    if request.args.get("key","") != AGENT_DASHBOARD_KEY:
        return "Access denied", 403
    return render_template("agent.html", conversations=conversations, key=AGENT_DASHBOARD_KEY)

@app.route("/agent/send", methods=["POST"])
def agent_send():
    if request.form.get("key","") != AGENT_DASHBOARD_KEY:
        return jsonify({"success":False}), 403
    phone_raw = request.form.get("phone","").strip()
    message   = request.form.get("message","").strip()
    if not phone_raw or not message:
        return jsonify({"success":False}), 400

    phone = normalize_phone(phone_raw)
    success = send_whatsapp_text(phone_raw, message)
    if phone in conversations:
        conversations[phone]["history"].append({
            "role":"agent","text":message,"time":datetime.now().strftime("%H:%M")
        })
        conversations[phone]["state"] = "handed_over"
        save_conversations()
    return jsonify({"success":success})

@app.route("/agent/book", methods=["POST"])
def agent_book():
    """Agent triggers booking flow for a specific lead."""
    if request.form.get("key","") != AGENT_DASHBOARD_KEY:
        return jsonify({"success":False}), 403
    phone_raw = request.form.get("phone","").strip()
    if not phone_raw:
        return jsonify({"success":False,"error":"phone required"}), 400

    phone = normalize_phone(phone_raw)
    conv  = get_conversation(phone)
    lang  = conv.get("language","english")

    # Generate available slots
    slots = get_available_slots()
    conv["booking_slots"] = [s.isoformat() for s in slots]
    conv["state"] = "booking_slots_sent"

    # Send slot list to lead
    slot_msg = format_slots_message(slots, lang)
    success = send_whatsapp_text(phone_raw, slot_msg)
    conv["history"].append({"role":"bot","text":slot_msg,"time":datetime.now().strftime("%H:%M")})
    save_conversations()

    return jsonify({
        "success": success,
        "message": "Booking slots sent to lead",
        "slots_count": len(slots)
    })

@app.route("/static/brochures/<filename>")
def serve_brochure(filename):
    return send_from_directory(os.path.join(os.path.dirname(__file__),"static","brochures"), filename)

@app.route("/thanks")
def thank_you():
    return render_template("thanks.html", name=request.args.get("name",""))

@app.route("/health")
def health():
    return jsonify({
        "status":"live",
        "conversations":len(conversations),
        "booked":sum(1 for c in conversations.values() if c.get("booking_confirmed")),
        "nurturing":sum(1 for c in conversations.values() if c.get("state")=="ai_nurturing")
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
        return jsonify({"history":conv.get("history",[]),"state":conv.get("state"),"booking_confirmed":conv.get("booking_confirmed",False),"booked_slot":conv.get("booked_slot")})
    return jsonify(conversations)

@app.route("/debug")
def debug():
    if request.args.get("key","") != AGENT_DASHBOARD_KEY:
        return "Access denied", 403
    lines = [
        f"GROQ_API_KEY: {'SET ('+GROQ_API_KEY[:8]+'...)' if GROQ_API_KEY else 'MISSING'}",
        f"WA_PHONE_NUMBER_ID: {'SET — '+WA_PHONE_NUMBER_ID if WA_PHONE_NUMBER_ID else 'MISSING'}",
        f"WA_ACCESS_TOKEN: {'SET ('+WA_ACCESS_TOKEN[:12]+'...)' if WA_ACCESS_TOKEN else 'MISSING'}",
        f"AGENT_WHATSAPP: {AGENT_WHATSAPP or 'NOT SET'}",
        f"VERIFY_TOKEN: {VERIFY_TOKEN}",
        f"RENDER_URL: {RENDER_URL}",
        f"Active conversations: {len(conversations)}",
        "", "--- Conversations ---",
    ]
    for ph, c in conversations.items():
        lines.append(f"+{ph} | {c.get('name','?')} | state={c.get('state')} | msgs={len(c.get('history',[]))} | booked={c.get('booking_confirmed')}")
    return "<pre style='font-family:monospace;padding:2rem;background:#111;color:#0f0;'>" + "\n".join(lines) + "</pre>"

# ── Startup ─────────────────────────────────────────────────────────────────────
load_conversations()
generate_brochures()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
