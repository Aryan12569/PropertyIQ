import os
import json
import requests
import re
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_from_directory
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT

load_dotenv()
app = Flask(__name__)

GROQ_API_KEY        = os.getenv("GROQ_API_KEY")
WA_PHONE_NUMBER_ID  = os.getenv("WA_PHONE_NUMBER_ID")
WA_ACCESS_TOKEN     = os.getenv("WA_ACCESS_TOKEN")
AGENT_WHATSAPP      = os.getenv("AGENT_WHATSAPP")
NOTION_TOKEN        = os.getenv("NOTION_TOKEN")
NOTION_DB_ID        = os.getenv("NOTION_DB_ID")
SPREADSHEET_ID      = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON   = os.getenv("GOOGLE_CREDS_JSON")
VERIFY_TOKEN        = os.getenv("VERIFY_TOKEN", "propertyiq2025")
RENDER_URL          = os.getenv("RENDER_URL", "https://propertyiq-q0ka.onrender.com")
AGENT_DASHBOARD_KEY = os.getenv("AGENT_DASHBOARD_KEY", "alnoor2025")

conversations = {}

PROPERTIES = [
    {"id":"mb_2br","name":"Muscat Bay — 2BR Apartment","type":"apartment_small","price":85000,"size":"120 sqm","floor":"4th Floor","features":["Direct sea view","Private balcony","Fully fitted kitchen","2 parking spaces","Pool and gym"],"status":"Available","handover":"Q2 2025","filename":"muscat_bay_2br.pdf"},
    {"id":"mb_3br","name":"Muscat Bay — 3BR Apartment","type":"apartment_large","price":130000,"size":"180 sqm","floor":"7th Floor","features":["Panoramic sea and mountain view","Maid room","Smart home system","2 parking spaces","Rooftop pool"],"status":"Available","handover":"Q2 2025","filename":"muscat_bay_3br.pdf"},
    {"id":"am_2br","name":"Al Mouj — 2BR Apartment","type":"apartment_small","price":95000,"size":"135 sqm","floor":"3rd Floor","features":["Golf course view","Italian marble kitchen","Oak flooring","1 parking space","Beach club access"],"status":"Last 2 units","handover":"Ready now","filename":"al_mouj_2br.pdf"},
    {"id":"am_villa","name":"Al Mouj — 4BR Villa","type":"villa","price":285000,"size":"380 sqm","floor":"Plot 500 sqm","features":["Private swimming pool","3-car garage","Smart home","Landscaped garden","Direct beach access"],"status":"1 unit only","handover":"Ready now","filename":"al_mouj_villa.pdf"},
    {"id":"tw_studio","name":"The Wave — 1BR Studio","type":"studio","price":55000,"size":"75 sqm","floor":"2nd Floor","features":["Marina view","Full furniture package option","Rental permit available","Hotel-managed option"],"status":"Available","handover":"Q1 2025","filename":"the_wave_studio.pdf"},
]

def generate_brochures():
    folder = os.path.join(os.path.dirname(__file__), "static", "brochures")
    os.makedirs(folder, exist_ok=True)
    GOLD  = colors.HexColor("#C9A84C")
    INK   = colors.HexColor("#1A1612")
    MUTED = colors.HexColor("#7A6F68")
    LIGHT = colors.HexColor("#FDF8EC")
    for prop in PROPERTIES:
        filepath = os.path.join(folder, prop["filename"])
        if os.path.exists(filepath):
            continue
        doc = SimpleDocTemplate(filepath, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
        story = []
        t_style  = ParagraphStyle("t",  fontName="Helvetica-Bold", fontSize=22, textColor=colors.white, leading=28, alignment=TA_CENTER)
        s_style  = ParagraphStyle("s",  fontName="Helvetica",      fontSize=11, textColor=colors.HexColor("#D4A017"), leading=16, alignment=TA_CENTER)
        lb_style = ParagraphStyle("lb", fontName="Helvetica-Bold", fontSize=9,  textColor=GOLD, leading=14)
        bd_style = ParagraphStyle("bd", fontName="Helvetica",      fontSize=10, textColor=MUTED, leading=16)
        pr_style = ParagraphStyle("pr", fontName="Helvetica-Bold", fontSize=28, textColor=GOLD, leading=34, alignment=TA_CENTER)
        ct_style = ParagraphStyle("ct", fontName="Helvetica",      fontSize=10, textColor=MUTED, leading=16, alignment=TA_CENTER)
        hdr = Table([
            [Paragraph("AL NOOR PROPERTIES", ParagraphStyle("hl", fontName="Helvetica-Bold", fontSize=10, textColor=GOLD, leading=14, alignment=TA_CENTER))],
            [Paragraph(prop["name"], t_style)],
            [Paragraph("Muscat, Sultanate of Oman", s_style)],
        ], colWidths=[170*mm])
        hdr.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),INK),
            ("TOPPADDING",(0,0),(-1,0),16),("BOTTOMPADDING",(0,0),(-1,0),4),
            ("TOPPADDING",(0,1),(-1,1),4),("BOTTOMPADDING",(0,1),(-1,1),4),
            ("TOPPADDING",(0,2),(-1,2),4),("BOTTOMPADDING",(0,2),(-1,2),16),
            ("LINEBELOW",(0,0),(-1,-1),3,GOLD),
        ]))
        story.append(hdr)
        story.append(Spacer(1,16))
        story.append(Paragraph(f"OMR {prop['price']:,}", pr_style))
        story.append(Spacer(1,8))
        story.append(HRFlowable(width="100%", thickness=0.5, color=GOLD))
        story.append(Spacer(1,16))
        details = [
            ["Type", prop["type"].replace("_"," ").title(), "Size", prop["size"]],
            ["Floor", prop["floor"], "Status", prop["status"]],
            ["Handover", prop["handover"], "Project", prop["name"].split("—")[0].strip()],
        ]
        det = Table([[Paragraph(r[0],lb_style),Paragraph(r[1],bd_style),Paragraph(r[2],lb_style),Paragraph(r[3],bd_style)] for r in details], colWidths=[30*mm,55*mm,35*mm,50*mm])
        det.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),LIGHT),
            ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#E8D5A3")),
            ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
            ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ]))
        story.append(det)
        story.append(Spacer(1,20))
        story.append(Paragraph("KEY FEATURES", lb_style))
        story.append(Spacer(1,8))
        feat = Table([[Paragraph("✦", ParagraphStyle("dot", fontName="Helvetica", fontSize=10, textColor=GOLD, leading=16)), Paragraph(f, bd_style)] for f in prop["features"]], colWidths=[8*mm,162*mm])
        feat.setStyle(TableStyle([("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),("LEFTPADDING",(0,0),(-1,-1),0),("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
        story.append(feat)
        story.append(Spacer(1,20))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E8D5A3")))
        story.append(Spacer(1,16))
        story.append(Paragraph("CONTACT YOUR SPECIALIST", lb_style))
        story.append(Spacer(1,8))
        story.append(Paragraph("Ahmed Al-Balushi  |  Senior Property Specialist", ct_style))
        story.append(Paragraph("Al Noor Properties, Muscat, Oman", ct_style))
        story.append(Paragraph("+968 9123 4567  |  info@alnoorproperties.om", ct_style))
        doc.build(story)
        print(f"Generated: {prop['filename']}")

def detect_language(text):
    return "arabic" if re.search(r'[\u0600-\u06FF]', text) else "english"

def get_conversation(phone):
    if phone not in conversations:
        conversations[phone] = {
            "state": "new",
            "language": "english",
            "name": None,
            "property_type": None,
            "budget": None,
            "timeline": None,
            "history": [],
            "booking_confirmed": False,
            "source": "whatsapp",
            "created_at": datetime.now().isoformat(),
        }
    return conversations[phone]

def add_to_history(phone, role, text):
    get_conversation(phone)["history"].append({
        "role": role,
        "text": text,
        "time": datetime.now().strftime("%H:%M")
    })

MESSAGES = {
    "english": {
        "welcome": "Hello! Welcome to Al Noor Properties 🏠\n\nI'm PropertyIQ, your personal property assistant. I'll help you find the perfect property in Muscat in just a few quick questions.\n\nMay I know your name?",
        "ask_type": "Thank you, {name}! 😊\n\nWhat type of property are you looking for?\n\n1️⃣  Apartment — 1 to 2 bedrooms\n2️⃣  Apartment — 3 or more bedrooms\n3️⃣  Villa\n4️⃣  Studio\n\nReply with a number or describe what you need.",
        "ask_budget": "What is your budget range?\n\n1️⃣  Under 60,000 OMR\n2️⃣  60,000 – 90,000 OMR\n3️⃣  90,000 – 130,000 OMR\n4️⃣  130,000 – 200,000 OMR\n5️⃣  Above 200,000 OMR\n\nReply with a number.",
        "ask_timeline": "Almost done! When are you planning to make a purchase?\n\n1️⃣  Immediately\n2️⃣  Within 3 months\n3️⃣  Within 6 months\n4️⃣  Just exploring for now\n\nReply with a number.",
        "unclear": "I didn't quite catch that. Could you reply with one of the numbered options above? 😊",
        "booking_confirmed_agent": "\n\nExcellent! 🎉 You're all set. Our specialist *Ahmed Al-Balushi* will personally reach out to confirm your appointment and prepare everything for your visit.\n\nAhmed Al-Balushi | Al Noor Properties | +968 9123 4567",
    },
    "arabic": {
        "welcome": "أهلاً وسهلاً! مرحباً بك في عقارات النور 🏠\n\nأنا PropertyIQ، مساعدك العقاري الشخصي. راح أساعدك تلقى العقار المثالي في مسقط بأسئلة بسيطة وسريعة.\n\nممكن أعرف اسمك؟",
        "ask_type": "شكراً {name}! 😊\n\nوش نوع العقار اللي تبحث عنه؟\n\n1️⃣  شقة صغيرة — غرفة أو غرفتين\n2️⃣  شقة كبيرة — ٣ غرف وأكثر\n3️⃣  فيلا\n4️⃣  استوديو\n\nردّ برقم أو وصف اللي تبحث عنه.",
        "ask_budget": "وش هي ميزانيتك تقريباً؟\n\n1️⃣  أقل من ٦٠٬٠٠٠ ريال عماني\n2️⃣  ٦٠٬٠٠٠ – ٩٠٬٠٠٠ ريال عماني\n3️⃣  ٩٠٬٠٠٠ – ١٣٠٬٠٠٠ ريال عماني\n4️⃣  ١٣٠٬٠٠٠ – ٢٠٠٬٠٠٠ ريال عماني\n5️⃣  أكثر من ٢٠٠٬٠٠٠ ريال عماني\n\nردّ برقم.",
        "ask_timeline": "آخر سؤال! متى تخطط تشتري العقار؟\n\n1️⃣  فوري\n2️⃣  خلال ٣ أشهر\n3️⃣  خلال ٦ أشهر\n4️⃣  بس أستكشف الحين\n\nردّ برقم.",
        "unclear": "ما فهمت بشكل واضح. ممكن تردّ بأحد الخيارات المرقمة أعلاه؟ 😊",
        "booking_confirmed_agent": "\n\nممتاز! 🎉 تم الحجز. متخصصنا *أحمد البلوشي* راح يتواصل معك شخصياً لتأكيد الموعد وتجهيز كل شيء لزيارتك.\n\nأحمد البلوشي | عقارات النور | ‎+968 9123 4567",
    }
}

# ─── AI SALES SYSTEM PROMPT ─────────────────────────────────────────────────

SALES_SYSTEM_PROMPT = """You are PropertyIQ, an elite real estate sales assistant for Al Noor Properties in Muscat, Oman. You are a world-class sales professional — warm, consultative, and highly persuasive. Your goal is to get the lead to commit to a viewing appointment.

AVAILABLE PROPERTIES:
1. Muscat Bay 2BR | 85,000 OMR | Sea view, balcony, pool+gym | Q2 2025
2. Muscat Bay 3BR | 130,000 OMR | Panoramic sea view, smart home, rooftop pool | Q2 2025
3. Al Mouj 2BR | 95,000 OMR | Golf view, marble kitchen, beach club | LAST 2 UNITS — ready now
4. Al Mouj 4BR Villa | 285,000 OMR | Private pool, beach access | ONLY 1 LEFT — ready now
5. The Wave Studio | 55,000 OMR | Marina view, rental permit | Q1 2025

AGENT: Ahmed Al-Balushi | +968 9123 4567

YOUR SALES APPROACH:
- Always respond in the LEAD'S LANGUAGE. Arabic leads get warm Gulf Khaleeji dialect.
- Keep messages under 130 words — punchy, not pushy.
- Use scarcity and urgency naturally: "only 2 units left", "this won't last the week".
- Ask ONE closing question per message to move them toward booking.
- Address objections confidently with facts and empathy.
- If they express ANY interest, pivot immediately to booking: "Would you like to schedule a private viewing this week?"
- Use their name naturally once per message.
- Closing signals to watch for: asking about payment, asking about viewing, saying "yes", "interested", "tell me more", "how do I proceed", "when can I see it".
- When they agree to a viewing or say they want to proceed: respond warmly confirming the booking and end your message with exactly the tag: [BOOKING_CONFIRMED]
- Never add a sign-off paragraph — it is appended separately.
- Format: natural WhatsApp paragraphs, no bullet points in conversational messages.
- Be REAL. Sound human, not like a chatbot. Use light emojis sparingly.
"""

RECOMMENDATION_SYSTEM_PROMPT = """You are PropertyIQ, a premium real estate assistant for Al Noor Properties in Muscat, Oman.

AVAILABLE PROPERTIES:
PROPERTY 1 | Muscat Bay 2BR Apartment | 85000 OMR | Sea view, balcony, pool, gym | Available Q2 2025
PROPERTY 2 | Muscat Bay 3BR Apartment | 130000 OMR | Panoramic sea view, smart home, rooftop pool | Available Q2 2025
PROPERTY 3 | Al Mouj 2BR Apartment | 95000 OMR | Golf view, marble kitchen, beach club | Last 2 units ready now
PROPERTY 4 | Al Mouj 4BR Villa | 285000 OMR | Private pool, 3-car garage, beach access | 1 unit ready now
PROPERTY 5 | The Wave 1BR Studio | 55000 OMR | Marina view, rental permit | Available Q1 2025

AGENT: Ahmed Al-Balushi | +968 9123 4567

RULES:
1. Write ENTIRELY in LEAD LANGUAGE. If Arabic use warm Gulf Arabic Khaleeji dialect only.
2. Keep under 120 words.
3. Greet by first name once only.
4. Recommend 1 property matching budget and type with one standout feature.
5. Mention their brochure has been sent.
6. End with a soft question to start conversation: ask if they'd like to know more or arrange a viewing.
7. Do NOT add sign-off — it is appended separately.
8. Format as WhatsApp message — natural paragraphs only.
"""

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
    bmap = {"1":"Under 60000 OMR","1️⃣":"Under 60000 OMR","2":"60000-90000 OMR","2️⃣":"60000-90000 OMR","3":"90000-130000 OMR","3️⃣":"90000-130000 OMR","4":"130000-200000 OMR","4️⃣":"130000-200000 OMR","5":"Above 200000 OMR","5️⃣":"Above 200000 OMR"}
    if t in bmap: return bmap[t]
    if any(w in t for w in ["under 60","less than 60","أقل"]): return "Under 60000 OMR"
    if "60" in t and "90" in t: return "60000-90000 OMR"
    if "90" in t and "130" in t: return "90000-130000 OMR"
    if "130" in t and "200" in t: return "130000-200000 OMR"
    if any(w in t for w in ["above 200","over 200","more than 200","أكثر"]): return "Above 200000 OMR"
    return None

def parse_timeline(text):
    t = text.lower().strip()
    tmap = {"1":"Immediately","1️⃣":"Immediately","2":"Within 3 months","2️⃣":"Within 3 months","3":"Within 6 months","3️⃣":"Within 6 months","4":"Just exploring","4️⃣":"Just exploring"}
    if t in tmap: return tmap[t]
    if any(w in t for w in ["now","immediate","asap","فوري","الحين"]): return "Immediately"
    if "3" in t or "three" in t: return "Within 3 months"
    if "6" in t or "six" in t: return "Within 6 months"
    if any(w in t for w in ["explor","look","أستكشف"]): return "Just exploring"
    return "Within 6 months"

def select_property(budget_str, prop_type_str):
    b = 0
    if "Under" in budget_str: b = 55000
    elif "60000-90000" in budget_str: b = 75000
    elif "90000-130000" in budget_str: b = 110000
    elif "130000-200000" in budget_str: b = 165000
    elif "Above" in budget_str: b = 300000
    if prop_type_str == "villa" and b >= 200000:
        return next((p for p in PROPERTIES if p["id"]=="am_villa"), PROPERTIES[0])
    if prop_type_str == "studio" or b <= 60000:
        return next((p for p in PROPERTIES if p["id"]=="tw_studio"), PROPERTIES[0])
    if prop_type_str == "apartment_large" and b >= 100000:
        return next((p for p in PROPERTIES if p["id"]=="mb_3br"), PROPERTIES[0])
    if b >= 90000:
        return next((p for p in PROPERTIES if p["id"]=="am_2br"), PROPERTIES[0])
    return next((p for p in PROPERTIES if p["id"]=="mb_2br"), PROPERTIES[0])

def call_groq(system_prompt, messages_payload, max_tokens=350, temperature=0.7):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "system", "content": system_prompt}] + messages_payload,
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Groq error: {e}")
        return None

def generate_ai_recommendation(name, budget, prop_type, timeline, language):
    user_content = f"LEAD NAME: {name}\nLEAD BUDGET: {budget}\nLEAD PROPERTY TYPE: {prop_type}\nLEAD TIMELINE: {timeline}\nLEAD LANGUAGE: {language}\n\nWrite the WhatsApp recommendation now."
    result = call_groq(RECOMMENDATION_SYSTEM_PROMPT, [{"role": "user", "content": user_content}], max_tokens=300)
    if result:
        return result
    return f"Hi {name}, thank you for your interest in Al Noor Properties. Based on your requirements we have found an excellent match for you." if language == "english" else f"مرحباً {name}، شكراً لاهتمامك بعقارات النور. لقينا لك خيارات ممتازة تناسب متطلباتك."

def generate_sales_response(conv, incoming_text):
    """Generate AI sales response using full conversation history."""
    lang = conv.get("language", "english")
    # Build conversation history for Groq
    history_for_ai = []
    for msg in conv.get("history", []):
        if msg["role"] == "user":
            history_for_ai.append({"role": "user", "content": msg["text"]})
        elif msg["role"] in ["bot", "agent"]:
            history_for_ai.append({"role": "assistant", "content": msg["text"]})

    # Add context about the lead
    context = f"""LEAD PROFILE:
Name: {conv.get('name', 'Unknown')}
Budget: {conv.get('budget', 'Unknown')}
Property Type: {conv.get('property_type', 'Unknown')}
Timeline: {conv.get('timeline', 'Unknown')}
Language: {lang}
Source: {conv.get('source', 'whatsapp')}

The lead just sent: {incoming_text}

Continue the sales conversation. Your goal is to secure a viewing appointment booking."""

    history_for_ai.append({"role": "user", "content": context})
    result = call_groq(SALES_SYSTEM_PROMPT, history_for_ai, max_tokens=350, temperature=0.72)
    return result

def send_whatsapp_text(to_number, message_text):
    clean = to_number.replace("+","").replace(" ","").replace("-","")
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product":"whatsapp","recipient_type":"individual","to":clean,"type":"text","text":{"preview_url":False,"body":message_text}}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        print(f"WhatsApp text sent to {clean}: {r.status_code}")
        return True
    except Exception as e:
        print(f"WhatsApp error to {clean}: {e}")
        return False

def send_whatsapp_document(to_number, pdf_url, filename):
    clean = to_number.replace("+","").replace(" ","").replace("-","")
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product":"whatsapp","recipient_type":"individual","to":clean,"type":"document","document":{"link":pdf_url,"filename":filename}}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        print(f"WhatsApp document sent to {clean}: {r.status_code}")
        return True
    except Exception as e:
        print(f"WhatsApp document error: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response: {e.response.text}")
        return False

def send_agent_alert(conv, phone):
    hot = "🔥 HOT LEAD" if conv.get("budget") and any(k in conv["budget"] for k in ["130000","200000","Above"]) else "🔔 NEW QUALIFIED LEAD"
    alert = (
        f"{hot} — BOOKING CONFIRMED\n\n"
        f"Name: {conv.get('name')}\n"
        f"Phone: +{phone}\n"
        f"Budget: {conv.get('budget')}\n"
        f"Property Type: {conv.get('property_type','').replace('_',' ').title()}\n"
        f"Timeline: {conv.get('timeline')}\n"
        f"Language: {'Arabic' if conv.get('language')=='arabic' else 'English'}\n"
        f"Source: {conv.get('source','WhatsApp').title()}\n\n"
        f"✅ Lead has confirmed a viewing appointment.\n"
        f"📋 Agent dashboard:\n"
        f"{RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}"
    )
    send_whatsapp_text(AGENT_WHATSAPP, alert)

def send_new_lead_alert(conv, phone):
    """Alert agent when a new lead comes in from the web form."""
    hot = "🔥 HOT LEAD" if conv.get("budget") and any(k in (conv.get("budget") or "") for k in ["130000","200000","Above"]) else "🔔 NEW LEAD"
    source = conv.get('source', 'whatsapp').title()
    alert = (
        f"{hot} — {source}\n\n"
        f"Name: {conv.get('name')}\n"
        f"Phone: +{phone}\n"
        f"Budget: {conv.get('budget')}\n"
        f"Property: {conv.get('property_type','').replace('_',' ').title()}\n"
        f"Language: {'Arabic' if conv.get('language')=='arabic' else 'English'}\n\n"
        f"✅ AI recommendation + brochure sent. Bot is now nurturing the lead.\n"
        f"📋 Dashboard: {RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}"
    )
    send_whatsapp_text(AGENT_WHATSAPP, alert)

def log_to_sheets(conv, phone, ai_response):
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Leads")
        hot = "YES" if any(k in (conv.get("budget") or "") for k in ["130000","200000","Above"]) else "NO"
        row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), conv.get("name",""), f"+{phone}", "", conv.get("budget",""), conv.get("property_type","").replace("_"," ").title(), conv.get("language",""), conv.get("timeline",""), ai_response, "New", hot, ""]
        sheet.append_row(row)
        print("Lead logged to Google Sheets.")
    except Exception as e:
        print(f"Google Sheets error: {e}")

def log_to_notion(conv, phone, ai_response):
    url = "https://api.notion.com/v1/pages"
    headers = {"Authorization": f"Bearer {NOTION_TOKEN}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
    hot = any(k in (conv.get("budget") or "") for k in ["130000","200000","Above"])
    clean_budget = (conv.get("budget") or "").replace(",","")
    clean_prop   = (conv.get("property_type") or "").replace(",","").replace("_"," ").title()
    clean_lang   = "Arabic" if conv.get("language") == "arabic" else "English"
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Lead Name": {"title": [{"text": {"content": conv.get("name","Unknown")}}]},
            "Phone": {"phone_number": f"+{phone}"},
            "Budget": {"multi_select": [{"name": clean_budget}]},
            "Property Type": {"multi_select": [{"name": clean_prop}]},
            "Language": {"multi_select": [{"name": clean_lang}]},
            "Their Message": {"rich_text": [{"text": {"content": conv.get("timeline","")}}]},
            "AI Response": {"rich_text": [{"text": {"content": ai_response}}]},
            "Status": {"select": {"name": "New"}},
            "Submitted At": {"date": {"start": datetime.now().isoformat()}},
            "Hot Lead": {"checkbox": hot}
        }
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        print("Lead logged to Notion.")
    except Exception as e:
        print(f"Notion error: {e}")

def process_message(phone, incoming_text):
    conv = get_conversation(phone)
    if conv["state"] in ["new", "asked_name"]:
        if detect_language(incoming_text) == "arabic":
            conv["language"] = "arabic"
    lang = conv["language"]
    add_to_history(phone, "user", incoming_text)
    msgs = MESSAGES[lang]

    # ── Initial qualification flow ──────────────────────────────────────────
    if conv["state"] == "new":
        conv["state"] = "asked_name"
        reply = msgs["welcome"]
        send_whatsapp_text(f"+{phone}", reply)
        add_to_history(phone, "bot", reply)
        return

    if conv["state"] == "asked_name":
        name = incoming_text.strip().split()[0].capitalize()
        conv["name"] = name
        conv["state"] = "asked_type"
        reply = msgs["ask_type"].format(name=name)
        send_whatsapp_text(f"+{phone}", reply)
        add_to_history(phone, "bot", reply)
        return

    if conv["state"] == "asked_type":
        prop_type = parse_property_type(incoming_text)
        if not prop_type:
            send_whatsapp_text(f"+{phone}", msgs["unclear"])
            return
        conv["property_type"] = prop_type
        conv["state"] = "asked_budget"
        reply = msgs["ask_budget"]
        send_whatsapp_text(f"+{phone}", reply)
        add_to_history(phone, "bot", reply)
        return

    if conv["state"] == "asked_budget":
        budget = parse_budget(incoming_text)
        if not budget:
            send_whatsapp_text(f"+{phone}", msgs["unclear"])
            return
        conv["budget"] = budget
        conv["state"] = "asked_timeline"
        reply = msgs["ask_timeline"]
        send_whatsapp_text(f"+{phone}", reply)
        add_to_history(phone, "bot", reply)
        return

    if conv["state"] == "asked_timeline":
        conv["timeline"] = parse_timeline(incoming_text)
        # Send initial AI recommendation
        ai_response = generate_ai_recommendation(conv["name"], conv["budget"], conv["property_type"], conv["timeline"], lang)
        send_whatsapp_text(f"+{phone}", ai_response)
        add_to_history(phone, "bot", ai_response)
        # Send brochure
        matched = select_property(conv["budget"], conv["property_type"])
        pdf_url = f"{RENDER_URL}/static/brochures/{matched['filename']}"
        send_whatsapp_document(f"+{phone}", pdf_url, matched["name"] + ".pdf")
        # Log to CRMs
        log_to_sheets(conv, phone, ai_response)
        log_to_notion(conv, phone, ai_response)
        # Alert agent that a new lead is being nurtured
        send_new_lead_alert(conv, phone)
        # Move to AI sales nurturing state
        conv["state"] = "ai_nurturing"
        return

    # ── AI sales nurturing state — bot continues until booking confirmed ────
    if conv["state"] == "ai_nurturing":
        ai_reply = generate_sales_response(conv, incoming_text)
        if not ai_reply:
            fallback = "Thank you for your message! Let me get our specialist Ahmed to reach out to you directly. He'll be in touch very soon 🙏" if lang == "english" else "شكراً على رسالتك! متخصصنا أحمد البلوشي راح يتواصل معك مباشرة قريباً 🙏"
            send_whatsapp_text(f"+{phone}", fallback)
            add_to_history(phone, "bot", fallback)
            return

        booking_confirmed = "[BOOKING_CONFIRMED]" in ai_reply
        clean_reply = ai_reply.replace("[BOOKING_CONFIRMED]", "").strip()

        if booking_confirmed:
            # Append booking confirmation sign-off
            full_reply = clean_reply + msgs["booking_confirmed_agent"]
            send_whatsapp_text(f"+{phone}", full_reply)
            add_to_history(phone, "bot", full_reply)
            conv["state"] = "handed_over"
            conv["booking_confirmed"] = True
            # Alert agent with booking
            send_agent_alert(conv, phone)
        else:
            send_whatsapp_text(f"+{phone}", clean_reply)
            add_to_history(phone, "bot", clean_reply)
        return

    # ── After handover — agent has taken over, still log incoming replies ───
    if conv["state"] == "handed_over":
        # If agent hasn't joined yet or lead replies, alert agent
        alert = (
            f"💬 LEAD REPLY — {conv.get('name', phone)}\n\n"
            f"Phone: +{phone}\n"
            f"Message: {incoming_text}\n\n"
            f"📋 Reply via dashboard:\n{RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}"
        )
        send_whatsapp_text(AGENT_WHATSAPP, alert)
        return


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def form():
    return render_template("form.html")

@app.route("/submit", methods=["POST"])
def submit():
    name      = request.form.get("name","").strip()
    phone     = request.form.get("phone","").strip()
    email     = request.form.get("email","").strip()
    budget    = request.form.get("budget","").strip()
    prop_type = request.form.get("property_type","").strip()
    language  = request.form.get("language","English").strip()
    message   = request.form.get("message","").strip()

    if not all([name, phone, email, budget, prop_type]):
        return "Missing required fields", 400

    lang_key = "arabic" if "arabic" in language.lower() else "english"
    clean_phone = phone.replace("+","").replace(" ","").replace("-","")

    # Map form prop_type to internal key
    prop_type_key = "apartment_small"
    if "3" in prop_type or "large" in prop_type.lower() or "3 or More" in prop_type: prop_type_key = "apartment_large"
    elif "villa" in prop_type.lower(): prop_type_key = "villa"
    elif "studio" in prop_type.lower(): prop_type_key = "studio"

    # Create/update conversation record
    conversations[clean_phone] = {
        "state": "ai_nurturing",
        "language": lang_key,
        "name": name.split()[0].capitalize(),
        "property_type": prop_type_key,
        "budget": budget,
        "timeline": message or "Web form enquiry",
        "history": [],
        "booking_confirmed": False,
        "source": "web_form",
        "created_at": datetime.now().isoformat(),
        "email": email,
    }
    conv = conversations[clean_phone]

    # Generate and send AI recommendation
    ai_response = generate_ai_recommendation(name, budget, prop_type, message or "Web form enquiry", lang_key)
    send_whatsapp_text(phone, ai_response)
    add_to_history(clean_phone, "bot", ai_response)

    # Send brochure
    matched = select_property(budget.replace(",",""), prop_type_key)
    pdf_url = f"{RENDER_URL}/static/brochures/{matched['filename']}"
    send_whatsapp_document(phone, pdf_url, matched["name"] + ".pdf")

    # Alert agent
    alert = (
        f"🔔 NEW LEAD — Web Form\n\n"
        f"Name: {name}\nPhone: {phone}\nEmail: {email}\n"
        f"Budget: {budget}\nProperty: {prop_type}\n"
        f"Language: {language}\nMessage: {message or 'None'}\n\n"
        f"✅ AI recommendation + brochure sent. Bot is now nurturing.\n"
        f"📋 Dashboard: {RENDER_URL}/agent?key={AGENT_DASHBOARD_KEY}"
    )
    send_whatsapp_text(AGENT_WHATSAPP, alert)

    log_to_sheets(conv, clean_phone, ai_response)
    log_to_notion(conv, clean_phone, ai_response)

    return redirect(url_for("thank_you", name=name.split()[0]))

@app.route("/webhook", methods=["GET"])
def webhook_verify():
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("Webhook verified.")
        return challenge, 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook_receive():
    data = request.get_json(silent=True)
    if not data:
        return "OK", 200
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value    = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    if msg.get("type") != "text":
                        continue
                    phone = msg.get("from","")
                    text  = msg.get("text",{}).get("body","").strip()
                    if phone and text:
                        print(f"Incoming from {phone}: {text}")
                        process_message(phone, text)
    except Exception as e:
        print(f"Webhook error: {e}")
    return "OK", 200

@app.route("/agent")
def agent_dashboard():
    key = request.args.get("key","")
    if key != AGENT_DASHBOARD_KEY:
        return "Access denied", 403
    return render_template("agent.html", conversations=conversations, key=AGENT_DASHBOARD_KEY)

@app.route("/agent/send", methods=["POST"])
def agent_send():
    key = request.form.get("key","")
    if key != AGENT_DASHBOARD_KEY:
        return jsonify({"success":False}), 403
    phone   = request.form.get("phone","").strip()
    message = request.form.get("message","").strip()
    if phone and message:
        full_phone = f"+{phone}" if not phone.startswith("+") else phone
        success = send_whatsapp_text(full_phone, message)
        if phone in conversations:
            add_to_history(phone, "agent", message)
            # Mark as handed over once agent sends a message
            if conversations[phone]["state"] != "handed_over":
                conversations[phone]["state"] = "handed_over"
        return jsonify({"success": success})
    return jsonify({"success":False}), 400

@app.route("/static/brochures/<filename>")
def serve_brochure(filename):
    folder = os.path.join(os.path.dirname(__file__), "static", "brochures")
    return send_from_directory(folder, filename)

@app.route("/thanks")
def thank_you():
    name = request.args.get("name","")
    return render_template("thanks.html", name=name)

@app.route("/health")
def health():
    total = len(conversations)
    booked = sum(1 for c in conversations.values() if c.get("booking_confirmed"))
    nurturing = sum(1 for c in conversations.values() if c.get("state") == "ai_nurturing")
    handed_over = sum(1 for c in conversations.values() if c.get("state") == "handed_over")
    return jsonify({
        "status": "PropertyIQ is live",
        "active_conversations": total,
        "booking_confirmed": booked,
        "ai_nurturing": nurturing,
        "handed_over": handed_over,
    }), 200

generate_brochures()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
