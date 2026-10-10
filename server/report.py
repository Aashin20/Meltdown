import json
import math
import os
import re
from datetime import datetime
from typing import List
from xml.sax.saxutils import escape

from pydantic import BaseModel
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.doughnut import Doughnut
from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, Rect, String, Wedge
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    CondPageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from pathlib import Path

from dotenv import load_dotenv

SERVER_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SERVER_DIR.parent
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(SERVER_DIR / ".env", override=False)
# --------------------------------------------------------------------------- #
# 1. GEMINI: structured report generation
# --------------------------------------------------------------------------- #


class Turn(BaseModel):
    speaker: str  # "Agent" or "Customer"
    text: str


class SentimentPoint(BaseModel):
    moment: str  # short label, max ~14 chars, e.g. "Opening", "Pricing"
    customer: int  # -100 (very negative) .. +100 (very positive)
    agent: int


class Objection(BaseModel):
    objection: str
    category: str  # Price / Timing / Authority / Need / Competition / Trust / Other
    agent_response: str
    effectiveness: int  # 1..5
    suggestion: str


class Skill(BaseModel):
    skill: str
    score: int  # 0..10


class ActionItem(BaseModel):
    task: str
    owner: str  # Agent / Customer / Both
    due: str
    priority: str  # High / Medium / Low


class UnresolvedConcern(BaseModel):
    concern: str
    category: str  # Price / Timing / Authority / Need / Competition / Trust / Technical / Other
    severity: str  # High / Medium / Low
    why_unresolved: str
    recommended_resolution: str


class FollowUpEmail(BaseModel):
    subject: str
    body: str  # full plain-text email: greeting, paragraphs, sign-off. Blank line between paragraphs.


class SalesCallReport(BaseModel):
    call_title: str
    customer_name: str
    company: str
    agent_name: str
    product_discussed: str
    call_outcome: str
    overall_score: int  # 0..100
    summary: str
    key_takeaways: List[str]
    conversation: List[Turn]
    sentiment_timeline: List[SentimentPoint]
    objections: List[Objection]
    unresolved_concerns: List[UnresolvedConcern]
    skills: List[Skill]
    strengths: List[str]
    improvements: List[str]
    action_items: List[ActionItem]
    follow_up_email: FollowUpEmail


PROMPT_TEMPLATE = """You are an expert sales coach and call analyst.

You are given two inputs about ONE sales call.

SOURCE RULES
- The TRANSCRIPT is the source of truth. Its speaker tags come from two audio channels:
  the MICROPHONE channel (tags like "Mic", "Microphone", "You", "Me") is the sales AGENT,
  and the SYSTEM AUDIO channel (tags like "System", "System audio", "Speaker", "Remote",
  or untagged remote voice) is the CUSTOMER.
- The GEMINI RESPONSES are earlier AI-generated notes / suggestions made about this call.
  Use them only as supporting context; never let them override the transcript.

YOUR TASK - return a JSON report that follows the provided schema:
1. conversation: rewrite the raw transcript as a clean, natural, chronological dialogue.
   - Use speaker "Agent" or "Customer" only.
   - Merge consecutive fragments from the same speaker into full sentences, fix punctuation,
     capitalisation and obvious speech-to-text errors, remove stutters and duplicated lines
     caused by audio bleed between the channels.
   - Do NOT invent, summarise or omit substantive content. Keep each turn under ~500 characters
     (split into more turns if needed).
2. summary: 4-6 sentence executive summary (who, what was discussed, key concerns, result).
3. key_takeaways: 3-5 short bullet strings.
4. call_outcome: very short phrase (e.g. "Demo booked", "Needs follow-up", "Lost - price").
5. overall_score: 0-100 judging the agent's performance and call effectiveness.
6. sentiment_timeline: 8-12 chronological points across the call. "moment" is a label of at most
   14 characters. customer and agent are integers from -100 to 100.
7. objections: every objection / concern the customer raised, with how the agent responded,
   effectiveness 1-5 and a concrete suggestion. Empty list if none.
8. unresolved_concerns: concerns that are STILL OPEN at the end of the call. Include:
   (a) objections or questions the customer raised that the agent did not answer, answered only
       partially, or answered unconvincingly;
   (b) hesitations the customer hinted at but never fully voiced (e.g. needing to check with a
       manager, budget uncertainty, comparing competitors, security/compliance doubts);
   (c) information the agent promised or the customer asked for but that was not delivered.
   For each give: concern (one sentence), category (Price / Timing / Authority / Need /
   Competition / Trust / Technical / Other), severity (High / Medium / Low - how likely it is to
   block the deal), why_unresolved (1-2 sentences grounded in the transcript), and
   recommended_resolution (a concrete way to close it in the follow-up). Do NOT list concerns the
   agent clearly resolved. Empty list if everything was resolved.
9. skills: exactly these 6 skills, each scored 0-10:
   Rapport Building, Discovery & Questions, Active Listening, Product Knowledge,
   Objection Handling, Closing & Next Steps.
10. strengths and improvements: 3-5 short strings each.
11. action_items: concrete next steps with owner (Agent/Customer/Both), due (e.g. "Within 2 days",
    "TBD") and priority (High/Medium/Low).
12. follow_up_email: a personalised follow-up email the AGENT can send to the CUSTOMER after this
    call. Return a subject and a body.
    - Open with a greeting using the customer's first name (if unknown, use "Hi there").
    - Personalise it: reference specific things the customer actually said (their goals, pain
      points, context, or a memorable moment) and the product discussed. Avoid generic filler.
    - Briefly recap what was agreed or discussed.
    - Address the unresolved_concerns directly and helpfully - answer them if the transcript
      gives enough information, otherwise say what you will send or arrange to resolve them.
    - Confirm the agreed next steps from action_items, with timing where known.
    - End with ONE clear, low-friction call to action (e.g. a proposed time for a short call).
    - Warm, professional, confident tone. 120-220 words. Plain text only - no markdown, no
      bullet symbols, no emoji. Separate paragraphs with a blank line. Sign off with the agent's
      name (if unknown, just "Best regards").
    - NEVER invent prices, discounts, dates, features, attachments or commitments that were not
      mentioned in the transcript.
If a name / company / product is unknown, use "Not mentioned". Be factual and concise.

===== TRANSCRIPT =====
{transcript}

===== GEMINI RESPONSES =====
{responses}
"""


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def analyze_call_with_gemini(
    transcript_path: str = "transcript.txt",
    responses_path: str = "gemini_responses.txt",
) -> dict:
    """Send transcript + earlier Gemini responses to Gemini and return the report as a dict."""
    from google import genai
    from google.genai import types

    model = os.environ.get("MODEL")
    if not model:
        raise EnvironmentError("Environment variable MODEL is not set.")

    prompt = PROMPT_TEMPLATE.format(
        transcript=_read(transcript_path) or "(empty)",
        responses=_read(responses_path) or "(empty)",
    )

    client = genai.Client()  # picks up GEMINI_API_KEY / GOOGLE_API_KEY
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SalesCallReport,
            temperature=0.2,
        ),
    )

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, SalesCallReport):
        return parsed.model_dump()
    if isinstance(parsed, dict):
        return parsed
    # Fallback: parse raw text (strip accidental ```json fences)
    raw = re.sub(r"^```(?:json)?|```$", "", (response.text or "").strip(), flags=re.M).strip()
    return json.loads(raw)


# --------------------------------------------------------------------------- #
# 2. PDF rendering
# --------------------------------------------------------------------------- #

NAVY = colors.HexColor("#0F172A")
BLUE = colors.HexColor("#2563EB")
TEAL = colors.HexColor("#14B8A6")
AMBER = colors.HexColor("#F59E0B")
RED = colors.HexColor("#EF4444")
GREEN = colors.HexColor("#22C55E")
SLATE = colors.HexColor("#475569")
MUTED = colors.HexColor("#94A3B8")
LIGHT = colors.HexColor("#F1F5F9")
BORDER = colors.HexColor("#E2E8F0")

PAGE_W, PAGE_H = letter
MARGIN = 0.6 * inch
CONTENT_W = PAGE_W - 2 * MARGIN - 12  # minus Frame's 6pt left/right padding


def _txt(s) -> str:
    """Plain text safe for ReportLab's built-in Helvetica (drops emoji / unsupported glyphs)."""
    s = str(s if s is not None else "")
    s = s.replace("\u2011", "-").replace("\u2192", "->").replace("\u2022", "-")
    return s.encode("cp1252", errors="ignore").decode("cp1252").strip()


def _p(s) -> str:

    return escape(_txt(s))


def _clamp(v, lo, hi, default=0):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _hex(col) -> str:
    """ReportLab colour -> '#RRGGBB' for use in <font color="..."> markup."""
    return col.hexval().replace("0x", "#")


def _styles():
    base = dict(fontName="Helvetica", textColor=SLATE, alignment=TA_LEFT)
    return {
        "body": ParagraphStyle("body", fontSize=9.6, leading=14, **base),
        "small": ParagraphStyle("small", fontSize=8.2, leading=11, **base),
        "cell": ParagraphStyle("cell", fontSize=8.6, leading=11.5, **base),
        "cellb": ParagraphStyle("cellb", fontSize=8.6, leading=11.5, fontName="Helvetica-Bold",
                                textColor=NAVY),
        "th": ParagraphStyle("th", fontSize=8, leading=10, fontName="Helvetica-Bold",
                             textColor=colors.white),
        "h": ParagraphStyle("h", fontSize=14, leading=17, fontName="Helvetica-Bold", textColor=NAVY),
        "bullet": ParagraphStyle("bullet", fontSize=9.2, leading=13, leftIndent=11, bulletIndent=0,
                                 **base),
        "kpi_v": ParagraphStyle("kpi_v", fontSize=20, leading=24, fontName="Helvetica-Bold",
                                textColor=NAVY, alignment=1),
        "kpi_l": ParagraphStyle("kpi_l", fontSize=7.5, leading=10, fontName="Helvetica",
                                textColor=MUTED, alignment=1),
        "meta_k": ParagraphStyle("meta_k", fontSize=7.4, leading=11, fontName="Helvetica-Bold",
                                 textColor=MUTED),
        "meta_v": ParagraphStyle("meta_v", fontSize=9, leading=11, fontName="Helvetica",
                                 textColor=NAVY),
        "meta_subj": ParagraphStyle("meta_subj", fontSize=9.4, leading=11, fontName="Helvetica-Bold",
                                    textColor=NAVY),
        "email": ParagraphStyle("email", fontSize=9.6, leading=14.5, fontName="Helvetica",
                                textColor=NAVY),
    }


# ---------- small drawing helpers ---------- #


def section_header(title, ST, color=BLUE):
    t = Table([[Paragraph(_p(title), ST["h"])]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, 0), 4, color),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _fit(text, font, size, max_w):
    """Truncate text with an ellipsis so it fits within max_w points."""
    text = _txt(text)
    if stringWidth(text, font, size) <= max_w:
        return text
    while text and stringWidth(text + "...", font, size) > max_w:
        text = text[:-1]
    return text.rstrip() + "..."


def banner(data, width):
    h = 108
    d = Drawing(width, h)
    d.add(Rect(0, 0, width, h, rx=14, ry=14, fillColor=NAVY, strokeColor=None))
    d.add(Rect(0, 0, 8, h, fillColor=TEAL, strokeColor=None))
    d.add(String(26, h - 20, "SALES CALL REPORT", fontName="Helvetica-Bold", fontSize=8.5,
                 fillColor=TEAL, charSpace=2))
    title = _fit("Security Discussion", "Helvetica-Bold", 22, width - 56)
    d.add(String(26, h - 48, title, fontName="Helvetica-Bold", fontSize=22, fillColor=colors.white))
    sub = f"Customer: {_txt(data.get('customer_name'))}  |  {_txt(data.get('company'))}"
    sub2 = f"Agent: {_txt(data.get('agent_name'))}  |  Product: RBI Security infrastructure"
    for i, line in enumerate((sub, sub2)):
        line = _fit(line, "Helvetica", 9.5, width - 190)
        d.add(String(26, h - 68 - i * 14, line, fontName="Helvetica", fontSize=9.5,
                     fillColor=colors.HexColor("#CBD5E1")))
    # outcome chip
    outcome = _txt(data.get("call_outcome") or "-")
    cw = min(150, stringWidth(outcome, "Helvetica-Bold", 9) + 28)
    cx = width - cw - 20
    d.add(Rect(cx, h - 32, cw, 22, rx=11, ry=11, fillColor=TEAL, strokeColor=None))
    d.add(String(cx + cw / 2, h - 25, outcome, fontName="Helvetica-Bold", fontSize=9,
                 fillColor=NAVY, textAnchor="middle"))
    d.add(String(width - 20, 14, datetime.now().strftime("%d %b %Y"), fontName="Helvetica",
                 fontSize=8.5, fillColor=MUTED, textAnchor="end"))
    return d


def gauge(score, size=150):
    score = _clamp(score, 0, 100)
    col = GREEN if score >= 75 else AMBER if score >= 50 else RED
    w, h = size, size * 0.68
    cx, cy, r = w / 2, 22, size / 2 - 6
    d = Drawing(w, h)
    d.add(Wedge(cx, cy, r, 0, 180, radius1=r - 15, fillColor=LIGHT, strokeColor=None))
    if score > 0:
        d.add(Wedge(cx, cy, r, 180 - 1.8 * score, 180, radius1=r - 15, fillColor=col,
                    strokeColor=None))
    d.add(String(cx, cy + 6, f"{int(score)}", fontName="Helvetica-Bold", fontSize=30, fillColor=NAVY,
                 textAnchor="middle"))
    d.add(String(cx, cy - 12, "OVERALL SCORE / 100", fontName="Helvetica-Bold", fontSize=6.8,
                 fillColor=MUTED, textAnchor="middle"))
    return d


def talk_ratio_chart(agent_pct, cust_pct, width=230, height=150):
    d = Drawing(width, height)
    dn = Doughnut()
    dn.x, dn.y, dn.width, dn.height = 10, 10, 130, 130
    dn.data = [max(agent_pct, 0.1), max(cust_pct, 0.1)]
    dn.slices.strokeColor = colors.white
    dn.slices.strokeWidth = 2
    dn.slices[0].fillColor = BLUE
    dn.slices[1].fillColor = TEAL
    dn.innerRadiusFraction = 0.62
    d.add(dn)
    d.add(String(75, 70, f"{agent_pct:.0f}%", fontName="Helvetica-Bold", fontSize=18, fillColor=NAVY,
                 textAnchor="middle"))
    d.add(String(75, 58, "agent talk", fontName="Helvetica", fontSize=7.5, fillColor=MUTED,
                 textAnchor="middle"))
    for i, (lab, pct, col) in enumerate((("Agent", agent_pct, BLUE), ("Customer", cust_pct, TEAL))):
        y = 95 - i * 30
        d.add(Rect(155, y, 10, 10, rx=2, ry=2, fillColor=col, strokeColor=None))
        d.add(String(171, y + 1, lab, fontName="Helvetica-Bold", fontSize=9, fillColor=NAVY))
        d.add(String(171, y - 11, f"{pct:.0f}% of words", fontName="Helvetica", fontSize=8,
                     fillColor=MUTED))
    return d


def sentiment_chart(points, width=CONTENT_W, height=190):
    d = Drawing(width, height)
    lc = HorizontalLineChart()
    lc.x, lc.y, lc.width, lc.height = 38, 52, width - 60, height - 78
    lc.data = [[_clamp(p.get("customer"), -100, 100) for p in points],
               [_clamp(p.get("agent"), -100, 100) for p in points]]
    lc.categoryAxis.categoryNames = [_txt(p.get("moment"))[:14] for p in points]
    lc.categoryAxis.labels.angle = 30
    lc.categoryAxis.labels.boxAnchor = "ne"
    lc.categoryAxis.labels.dx = 4
    lc.categoryAxis.labels.dy = -4
    lc.categoryAxis.labels.fontSize = 7.5
    lc.categoryAxis.labels.fillColor = SLATE
    lc.categoryAxis.joinAxisMode = "bottom"
    lc.categoryAxis.strokeColor = BORDER
    lc.valueAxis.valueMin, lc.valueAxis.valueMax, lc.valueAxis.valueStep = -100, 100, 50
    lc.valueAxis.labels.fontSize = 7.5
    lc.valueAxis.labels.fillColor = SLATE
    lc.valueAxis.visibleGrid = 1
    lc.valueAxis.gridStrokeColor = BORDER
    lc.valueAxis.strokeColor = BORDER
    for i, col in enumerate((TEAL, BLUE)):
        lc.lines[i].strokeColor = col
        lc.lines[i].strokeWidth = 2.4
        lc.lines[i].symbol = makeMarker("FilledCircle", size=5, fillColor=col, strokeColor=colors.white)
    d.add(lc)
    # legend
    for i, (lab, col) in enumerate((("Customer", TEAL), ("Agent", BLUE))):
        x = width - 150 + i * 75
        d.add(Rect(x, height - 14, 9, 9, rx=2, ry=2, fillColor=col, strokeColor=None))
        d.add(String(x + 13, height - 13, lab, fontName="Helvetica", fontSize=8, fillColor=SLATE))
    d.add(String(38, height - 13, "Sentiment (-100 negative  to  +100 positive)", fontName="Helvetica",
                 fontSize=8, fillColor=MUTED))
    return d


def radar_chart(skills, size=250):
    d = Drawing(size, size)
    n = len(skills)
    if n < 3:
        return d
    cx = cy = size / 2
    R = size / 2 - 52

    def pt(i, frac):
        a = math.pi / 2 - 2 * math.pi * i / n
        return cx + R * frac * math.cos(a), cy + R * frac * math.sin(a)

    for ring in (0.2, 0.4, 0.6, 0.8, 1.0):
        pts = [c for i in range(n) for c in pt(i, ring)]
        d.add(Polygon(pts, fillColor=LIGHT if ring == 1.0 else None, strokeColor=BORDER,
                      strokeWidth=0.8))
    for i in range(n):
        x, y = pt(i, 1)
        d.add(Line(cx, cy, x, y, strokeColor=BORDER, strokeWidth=0.8))
    vals = [_clamp(s.get("score"), 0, 10) / 10 for s in skills]
    poly = [c for i, v in enumerate(vals) for c in pt(i, v)]
    d.add(Polygon(poly, fillColor=BLUE, fillOpacity=0.28, strokeColor=BLUE, strokeWidth=2.2))
    for i, v in enumerate(vals):
        x, y = pt(i, v)
        d.add(Circle(x, y, 3.4, fillColor=BLUE, strokeColor=colors.white, strokeWidth=1))
    for i, s in enumerate(skills):
        x, y = pt(i, 1.0)
        lx, ly = pt(i, 1.2)
        ca = math.cos(math.pi / 2 - 2 * math.pi * i / n)
        anchor = "middle" if abs(ca) < 0.3 else ("start" if ca > 0 else "end")
        name = _txt(s.get("skill"))
        words = name.replace(" & ", " &\n").split("\n") if " & " in name else \
            ([name] if len(name) < 14 else [" ".join(name.split()[:1]), " ".join(name.split()[1:])])
        for k, wline in enumerate(words):
            d.add(String(lx, ly - 3 - k * 9, wline, fontName="Helvetica-Bold", fontSize=7.6,
                         fillColor=NAVY, textAnchor=anchor))
        d.add(String(lx, ly - 3 - len(words) * 9, f"{_clamp(s.get('score'), 0, 10):.0f}/10",
                     fontName="Helvetica", fontSize=7.4, fillColor=BLUE, textAnchor=anchor))
    return d


def effectiveness_bar(score, w=62, h=9):
    score = int(_clamp(score, 0, 5))
    col = GREEN if score >= 4 else AMBER if score == 3 else RED
    d = Drawing(w, h + 2)
    seg = (w - 8) / 5
    for i in range(5):
        d.add(Rect(i * (seg + 2), 1, seg, h, rx=2, ry=2, fillColor=col if i < score else BORDER,
                   strokeColor=None))
    return d


def checkbox(size=11):
    d = Drawing(size + 2, size + 2)
    d.add(Rect(1, 1, size, size, rx=2.5, ry=2.5, fillColor=colors.white, strokeColor=BLUE,
               strokeWidth=1.3))
    return d


# ---------- new sections: unresolved concerns + follow-up email ---------- #

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SEVERITY_COLOR = {"high": RED, "medium": AMBER, "low": GREEN}


def unresolved_concerns_table(concerns, ST):
    """Table of concerns still open at the end of the call, most severe first."""
    concerns = sorted(
        concerns,
        key=lambda c: SEVERITY_ORDER.get(_txt(c.get("severity")).lower(), 3),
    )
    rows = [[Paragraph(h, ST["th"]) for h in
             ("CONCERN", "SEVERITY", "WHY IT'S UNRESOLVED", "RECOMMENDED RESOLUTION")]]
    for c in concerns:
        sev = _txt(c.get("severity") or "Medium")
        col = SEVERITY_COLOR.get(sev.lower(), AMBER)
        rows.append([
            [Paragraph(_p(c.get("concern")), ST["cellb"]),
             Paragraph(f'<font color="#94A3B8">{_p(c.get("category"))}</font>', ST["small"])],
            Paragraph(f'<font color="{_hex(col)}"><b>{_p(sev.upper())}</b></font>', ST["small"]),
            Paragraph(_p(c.get("why_unresolved")), ST["cell"]),
            Paragraph(_p(c.get("recommended_resolution")), ST["cell"]),
        ])
    t = Table(rows, colWidths=[CONTENT_W * 0.27, CONTENT_W * 0.11, CONTENT_W * 0.28, CONTENT_W * 0.34],
              repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def follow_up_email_card(email, data, ST):
    """Email 'card': To / From / Subject header plus one table row per paragraph (so it can
    split cleanly across pages)."""
    pad = 14
    inner_w = CONTENT_W - 2 * pad

    to_name = _txt(data.get("customer_name"))
    company = _txt(data.get("company"))
    to_line = to_name if to_name and to_name.lower() != "not mentioned" else "Customer"
    if company and company.lower() != "not mentioned":
        to_line += f"  ({company})"
    agent = _txt(data.get("agent_name"))
    from_line = agent if agent and agent.lower() != "not mentioned" else "Sales Agent"

    meta = Table(
        [[Paragraph("TO", ST["meta_k"]), Paragraph(_p(to_line), ST["meta_v"])],
         [Paragraph("FROM", ST["meta_k"]), Paragraph(_p(from_line), ST["meta_v"])],
         [Paragraph("SUBJECT", ST["meta_k"]), Paragraph(_p(email.get("subject")), ST["meta_subj"])]],
        colWidths=[52, inner_w - 52],
    )
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    rows = [[meta]]
    body = str(email.get("body") or "").replace("\r\n", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    for para in paragraphs:
        # keep single line breaks (e.g. "Best regards,\nName") inside a paragraph
        html = "<br/>".join(_p(line) for line in para.split("\n") if line.strip())
        rows.append([Paragraph(html, ST["email"])])

    t = Table(rows, colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, BORDER),
        ("LINEBEFORE", (0, 0), (0, -1), 4, BLUE),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), pad), ("RIGHTPADDING", (0, 0), (-1, -1), pad),
        ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, 0), (-1, 0), 9),
        ("TOPPADDING", (0, 1), (-1, -1), 5), ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("TOPPADDING", (0, 1), (-1, 1), 12),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 14),
    ]))
    return t


# ---------- chat bubble flowable ---------- #


class ChatBubble(Flowable):
    AV = 24  # avatar column

    def __init__(self, speaker, text, avail_w, show_label=True):
        super().__init__()
        self.is_agent = speaker.lower().startswith("a")
        self.text = _txt(text)
        self.show_label = show_label
        self.avail_w = avail_w
        self.style = ParagraphStyle("bub", fontName="Helvetica", fontSize=9.2, leading=13,
                                    textColor=colors.white if self.is_agent else NAVY)

    def wrap(self, aw, ah):
        self.avail_w = aw
        max_bw = aw * 0.74
        natural = stringWidth(self.text, "Helvetica", 9.2) + 26
        self.bw = min(max_bw, max(natural, 80))
        self.para = Paragraph(escape(self.text), self.style)
        _, ph = self.para.wrap(self.bw - 26, ah)
        self.ph = ph
        self.label_h = 12 if self.show_label else 0
        self.height = ph + 14 + self.label_h + 5
        return aw, self.height

    def draw(self):
        c = self.canv
        bx = self.avail_w - self.AV - self.bw if self.is_agent else self.AV
        by = 5
        bh = self.ph + 14
        fill = BLUE if self.is_agent else LIGHT
        c.setFillColor(fill)
        c.setStrokeColor(fill if self.is_agent else BORDER)
        c.roundRect(bx, by, self.bw, bh, 9, stroke=1, fill=1)
        self.para.drawOn(c, bx + 13, by + 7)
        if self.show_label:
            label = "Agent" if self.is_agent else "Customer"
            c.setFont("Helvetica-Bold", 7.4)
            c.setFillColor(BLUE if self.is_agent else TEAL)
            lx = bx + self.bw if self.is_agent else bx
            (c.drawRightString if self.is_agent else c.drawString)(lx, by + bh + 3, label.upper())
            # avatar
            ax = self.avail_w - self.AV / 2 + 2 if self.is_agent else self.AV / 2 - 2
            c.setFillColor(BLUE if self.is_agent else TEAL)
            c.circle(ax, by + bh - 9, 9, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 8)
            c.drawCentredString(ax, by + bh - 12, label[0])


def _split_long(text, limit=650):
    text = _txt(text)
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if cur and len(cur) + len(sent) > limit:
            parts.append(cur.strip())
            cur = ""
        cur += sent + " "
    if cur.strip():
        parts.append(cur.strip())
    return parts


# ---------- page furniture ---------- #


def _on_page(canvas, doc, title):
    canvas.saveState()
    if doc.page > 1:
        canvas.setStrokeColor(BORDER)
        canvas.line(MARGIN, PAGE_H - 0.5 * inch, PAGE_W - MARGIN, PAGE_H - 0.5 * inch)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(MARGIN, PAGE_H - 0.43 * inch, "SALES CALL REPORT")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.43 * inch, _txt(title)[:70])
    canvas.setStrokeColor(BORDER)
    canvas.line(MARGIN, 0.5 * inch, PAGE_W - MARGIN, 0.5 * inch)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 0.34 * inch, "Generated by AI - review before sharing")
    canvas.drawRightString(PAGE_W - MARGIN, 0.34 * inch, f"Page {doc.page}")
    canvas.restoreState()


# ---------- main builder ---------- #


def build_report_pdf(data: dict, output_path: str = "sales_call_report.pdf") -> str:
    ST = _styles()
    story = []

    conversation = [t for t in (data.get("conversation") or []) if t.get("text")]
    skills = data.get("skills") or []
    sentiment = data.get("sentiment_timeline") or []
    objections = data.get("objections") or []
    unresolved = data.get("unresolved_concerns") or []
    actions = data.get("action_items") or []
    email = data.get("follow_up_email") or {}

    # talk-to-listen from the cleaned conversation (word counts)
    a_words = sum(len(t["text"].split()) for t in conversation if t["speaker"].lower().startswith("a"))
    c_words = sum(len(t["text"].split()) for t in conversation if not t["speaker"].lower().startswith("a"))
    total = max(a_words + c_words, 1)
    agent_pct, cust_pct = 100 * a_words / total, 100 * c_words / total

    # ---- Banner ----
    story += [banner(data, CONTENT_W), Spacer(1, 14)]

    # ---- Gauge + summary ----
    summary_cell = [
        Paragraph("<b>Executive Summary</b>", ParagraphStyle("sh", parent=ST["body"], textColor=NAVY,
                                                             fontSize=11, spaceAfter=4)),
        Paragraph(_p(data.get("summary")), ST["body"]),
    ]
    top = Table([[gauge(data.get("overall_score", 0)), summary_cell]], colWidths=[165, CONTENT_W - 165])
    top.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.8, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEAFTER", (0, 0), (0, 0), 0.8, BORDER),
    ]))
    story += [top, Spacer(1, 12)]

    # ---- KPI cards ----
    avg_cust = (sum(_clamp(p.get("customer"), -100, 100) for p in sentiment) / len(sentiment)) if sentiment else 0
    kpis = [
        (f"{avg_cust:+.0f}", "AVG CUSTOMER SENTIMENT", GREEN if avg_cust > 15 else AMBER if avg_cust > -15 else RED),
        (str(len(objections)), "OBJECTIONS RAISED", AMBER),
        (str(len(unresolved)), "UNRESOLVED CONCERNS", RED if unresolved else GREEN),
        (str(len(actions)), "ACTION ITEMS", BLUE),
        (str(len(conversation)), "CONVERSATION TURNS", TEAL),
    ]
    kw = CONTENT_W / len(kpis)
    cards = Table([[[Paragraph(v, ST["kpi_v"]), Paragraph(l, ST["kpi_l"])] for v, l, _ in kpis]],
                  colWidths=[kw] * len(kpis))
    style = [("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
             ("BACKGROUND", (0, 0), (-1, -1), LIGHT)]
    for i, (_, _, col) in enumerate(kpis):
        style += [("LINEABOVE", (i, 0), (i, 0), 3, col),
                  ("LINEAFTER", (i, 0), (i, 0), 6, colors.white)]
    cards.setStyle(TableStyle(style))
    story += [cards, Spacer(1, 14)]

    # ---- Key takeaways ----
    takeaways = [Paragraph(_p(t), ST["bullet"], bulletText="\u2022") for t in data.get("key_takeaways") or []]
    if takeaways:
        story.append(KeepTogether([section_header("Key Takeaways", ST, TEAL), Spacer(1, 6)] + takeaways))
        story.append(Spacer(1, 14))

    # ---- Talk-to-listen + sentiment ----
    verdict = ("Agent dominated the conversation - aim for 40-60% talk time with more open questions."
               if agent_pct > 65 else
               "Customer did most of the talking - good listening, make sure the agent guides the call."
               if agent_pct < 35 else
               "Balanced conversation - healthy talk-to-listen ratio (ideal: 40-60% agent).")
    ratio_tbl = Table([[talk_ratio_chart(agent_pct, cust_pct), Paragraph(_p(verdict), ST["body"])]],
                      colWidths=[245, CONTENT_W - 245])
    ratio_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOX", (0, 0), (-1, -1), 0.8, BORDER),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
    story.append(KeepTogether([section_header("Talk-to-Listen Ratio", ST, BLUE), Spacer(1, 6), ratio_tbl]))
    story.append(Spacer(1, 14))

    if sentiment:
        story.append(KeepTogether([section_header("Sentiment Timeline", ST, TEAL), Spacer(1, 6),
                                   sentiment_chart(sentiment)]))
        story.append(Spacer(1, 14))

    # ---- Skills scorecard + strengths / improvements ----
    def bullets(items, col):
        return [Paragraph(_p(i), ST["bullet"], bulletText="\u2022") for i in items]

    right = [Paragraph("<b>Strengths</b>", ParagraphStyle("g", parent=ST["body"], textColor=GREEN, fontSize=10.5, spaceAfter=3))]
    right += bullets(data.get("strengths") or [], GREEN)
    right += [Spacer(1, 8), Paragraph("<b>Areas to Improve</b>", ParagraphStyle("r", parent=ST["body"], textColor=RED, fontSize=10.5, spaceAfter=3))]
    right += bullets(data.get("improvements") or [], RED)
    score_tbl = Table([[radar_chart(skills), right]], colWidths=[265, CONTENT_W - 265])
    score_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOX", (0, 0), (-1, -1), 0.8, BORDER),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                   ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(KeepTogether([section_header("Skills Scorecard", ST, BLUE), Spacer(1, 6), score_tbl]))
    story.append(Spacer(1, 14))

    # ---- Objections ----
    story.append(section_header("Objections & Handling", ST, AMBER))
    story.append(Spacer(1, 6))
    if objections:
        rows = [[Paragraph(h, ST["th"]) for h in ("OBJECTION", "AGENT RESPONSE", "RATING", "COACHING TIP")]]
        for o in objections:
            rows.append([
                [Paragraph(_p(o.get("objection")), ST["cellb"]),
                 Paragraph(f'<font color="#94A3B8">{_p(o.get("category"))}</font>', ST["small"])],
                Paragraph(_p(o.get("agent_response")), ST["cell"]),
                effectiveness_bar(o.get("effectiveness", 0)),
                Paragraph(_p(o.get("suggestion")), ST["cell"]),
            ])
        t = Table(rows, colWidths=[CONTENT_W * 0.25, CONTENT_W * 0.28, CONTENT_W * 0.15, CONTENT_W * 0.32],
                  repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("No objections were raised during this call.", ST["body"]))
    story.append(Spacer(1, 14))

    # ---- Unresolved concerns ----
    story.append(CondPageBreak(1.6 * inch))
    story.append(section_header("Unresolved Concerns", ST, RED))
    story.append(Spacer(1, 6))
    if unresolved:
        story.append(Paragraph(
            "Open issues that were not fully answered during the call. "
            "Address these in the follow-up to keep the deal moving.", ST["small"]))
        story.append(Spacer(1, 6))
        story.append(unresolved_concerns_table(unresolved, ST))
    else:
        story.append(Paragraph("No unresolved concerns - everything the customer raised was addressed.",
                               ST["body"]))
    story.append(Spacer(1, 14))

    # ---- Action items ----
    story.append(CondPageBreak(1.6 * inch))
    story.append(section_header("Action Items & Next Steps", ST, GREEN))
    story.append(Spacer(1, 6))
    if actions:
        pcol = {"high": RED, "medium": AMBER, "low": GREEN}
        rows = [[Paragraph(h, ST["th"]) for h in ("", "TASK", "OWNER", "DUE", "PRIORITY")]]
        for i, a in enumerate(actions, start=1):
            pr = _txt(a.get("priority") or "Medium")
            col = pcol.get(pr.lower(), AMBER)
            rows.append([checkbox(), Paragraph(_p(a.get("task")), ST["cell"]),
                         Paragraph(_p(a.get("owner")), ST["cell"]), Paragraph(_p(a.get("due")), ST["cell"]),
                         Paragraph(f'<font color="{_hex(col)}"><b>{_p(pr.upper())}</b></font>',
                                   ST["small"])])
        t = Table(rows, colWidths=[24, CONTENT_W * 0.52, CONTENT_W * 0.14, CONTENT_W * 0.16, CONTENT_W * 0.10],
                  repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("No follow-up actions were identified.", ST["body"]))
    story.append(Spacer(1, 14))

    # ---- Personalised follow-up email ----
    if str(email.get("body") or "").strip():
        story.append(CondPageBreak(2.4 * inch))
        story.append(section_header("Personalized Follow-Up Email", ST, BLUE))
        story.append(Spacer(1, 4))
        story.append(Spacer(1, 6))
        story.append(follow_up_email_card(email, data, ST))

    # ---- Conversation ----
    story += [Spacer(1, 16), CondPageBreak(2.2 * inch)]

    prev = None
    for turn in conversation:
        speaker = "Agent" if turn["speaker"].lower().startswith("a") else "Customer"
        for k, chunk in enumerate(_split_long(turn["text"])):
            story.append(ChatBubble(speaker, chunk, CONTENT_W, show_label=(k == 0 and speaker != prev) or k == 0))
        prev = speaker

    title = data.get("call_title") or "Sales Call"
    doc = SimpleDocTemplate(output_path, pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title=f"Sales Call Report - {_txt(title)}", author="Sales Call Analyzer")
    on_page = lambda c, d: _on_page(c, d, title)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return output_path


# --------------------------------------------------------------------------- #
# 3. One-call wrapper
# --------------------------------------------------------------------------- #


def generate_sales_call_report(
    transcript_path: str | Path = PROJECT_DIR / "transcript.txt",
    responses_path: str | Path = PROJECT_DIR / "gemini_responses.txt",
    output_path: str | Path = PROJECT_DIR / "sales_call_report.pdf",
    save_json: bool = True,
) -> str:
    """transcript.txt + gemini_responses.txt -> Gemini -> styled PDF. Returns the PDF path."""
    data = analyze_call_with_gemini(transcript_path, responses_path)
    if save_json:  # handy for debugging / re-rendering without another API call
        with open(os.path.splitext(output_path)[0] + ".json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    return build_report_pdf(data, output_path)


if __name__ == "__main__":
    print("Saved:", generate_sales_call_report())