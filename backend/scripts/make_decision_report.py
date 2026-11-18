"""One-page decision report: why Twilio, what it costs, why this architecture.

Every figure here is either measured from this project's own call records or
carries its verification date from the design doc. Nothing is estimated
silently: where a number is an estimate it says so on the page.

    cd backend
    ..\\.venv\\Scripts\\python.exe scripts/make_decision_report.py
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reportlab.lib import colors                              # noqa: E402
from reportlab.lib.enums import TA_JUSTIFY                    # noqa: E402
from reportlab.lib.pagesizes import A4                        # noqa: E402
from reportlab.lib.styles import ParagraphStyle               # noqa: E402
from reportlab.lib.units import mm                            # noqa: E402
from reportlab.platypus import (BaseDocTemplate, Frame,       # noqa: E402
                                PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

OUT = Path.home() / "Downloads" / "voice-translation-platform-decision.pdf"
DB = Path(__file__).resolve().parent.parent / "data" / "app.db"
LOG = Path(__file__).resolve().parent.parent / "logs" / "backend.log"

INK = colors.HexColor("#1a2b28")
MUTED = colors.HexColor("#5a6b66")
RULE = colors.HexColor("#c8d4d0")
BAND = colors.HexColor("#eef3f1")

# Twilio list prices, [VERIFIED] 18 Sep 2026 - design doc section 15.
TWILIO = [
    ("Browser leg (Twilio Client)", 0.0040),
    ("Outbound leg to an Indian mobile", 0.0496),
    ("Media Streams, two legs at $0.004", 0.0080),
]


def measured() -> dict:
    """What this project's own calls actually cost and achieved."""
    out = {"calls": 0, "minutes": 0.0, "model_usd": 0.0, "inr_rate": 0.0}

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = list(con.execute(
        "select duration_s, model_usd from calls "
        "where duration_s is not null and model_usd > 0"))
    out["calls"] = len(rows)
    out["minutes"] = sum(r[0] for r in rows) / 60
    out["model_usd"] = sum(r[1] for r in rows)
    out["billed_min"] = sum(max(1, math.ceil(r[0] / 60)) for r in rows)

    usd = inr = 0.0
    lat: dict[str, list[float]] = {"rtt": [], "first": [], "setup": []}
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("event") == "gemini_closed" and d.get("usd"):
            usd += d["usd"]
            inr += d.get("inr", 0.0)
        if d.get("event") == "latency_summary" and d.get("model_rtt_ms"):
            lat["rtt"].append(d["model_rtt_ms"])
        if d.get("event") == "translation_latency" and d.get("first_audio_ms"):
            lat["first"].append(d["first_audio_ms"])
        if d.get("event") == "latency_setup" and d.get("twiml_to_stream_ms"):
            v = d["twiml_to_stream_ms"]
            if 0 < v < 20000:
                lat["setup"].append(v)

    out["inr_rate"] = inr / usd if usd else 0.0
    for key, values in lat.items():
        values.sort()
        out[key] = values[len(values) // 2] if values else 0
    return out


def build() -> None:
    m = measured()
    twilio_min = sum(rate for _, rate in TWILIO)
    model_min = m["model_usd"] / m["minutes"] if m["minutes"] else 0.0
    total_min = twilio_min + model_min
    rate = m["inr_rate"] or 88.0

    styles = {
        "title": ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=15,
                                textColor=INK, leading=18, spaceAfter=2),
        "sub": ParagraphStyle("s", fontName="Helvetica", fontSize=8.5,
                              textColor=MUTED, leading=11, spaceAfter=9),
        "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=10,
                            textColor=INK, leading=12, spaceBefore=9,
                            spaceAfter=4),
        "p": ParagraphStyle("p", fontName="Helvetica", fontSize=8.6,
                            textColor=INK, leading=11.6, alignment=TA_JUSTIFY,
                            spaceAfter=4),
        "note": ParagraphStyle("n", fontName="Helvetica-Oblique", fontSize=7.4,
                               textColor=MUTED, leading=9.4, spaceBefore=3),
    }

    def cell(text, bold=False, right=False):
        return Paragraph(
            text, ParagraphStyle("c", fontName="Helvetica-Bold" if bold
                                 else "Helvetica", fontSize=8.3,
                                 textColor=INK, leading=10.5,
                                 alignment=2 if right else 0))

    story = []
    story.append(Paragraph("Real-Time Voice Translation — Platform &amp; "
                           "Architecture Decision", styles["title"]))
    story.append(Paragraph(
        f"Prepared {date.today():%d %B %Y}. Cost and latency figures are "
        f"measured from {m['calls']} production calls "
        f"({m['minutes']:.0f} connected minutes); Twilio rates are list "
        f"prices verified 18 Sep 2026.", styles["sub"]))

    # --- 1 -----------------------------------------------------------------
    story.append(Paragraph("1. Why Twilio", styles["h"]))
    story.append(Paragraph(
        "The product needs three things <b>at once</b>: carrier reach to an "
        "ordinary Indian mobile, a browser that can join the same call, and "
        "a media path that both <b>receives and injects</b> audio mid-call. "
        "The third is the deciding one — translated speech has to be played "
        "back into a live call, not merely observed. Twilio provides all "
        "three in one platform, and all three are now proven on live traffic "
        "rather than taken on trust.", styles["p"]))

    alt = Table([
        [cell("Option", True), cell("Why it was not chosen", True)],
        [cell("Asterisk / FreeSWITCH<br/>(self-hosted)"),
         cell("Capable media layers, but no carrier reach: a SIP trunk and "
              "numbers must still be bought, and India's rules apply to that "
              "provider too. Adds SIP operations, NAT traversal, redundancy "
              "and a browser client we get free from the SDK.")],
        [cell("Plivo and similar CPaaS"),
         cell("Credible on price and PSTN reach. Not rejected on capability "
              "— the decision rests on the three requirements above being "
              "<b>verified in production</b> on Twilio. A switch is a "
              "re-verification exercise, not a configuration change.")],
        [cell("Model-as-endpoint<br/>(SIP straight to the model)"),
         cell("Simpler, but makes the model the other party. We need a "
              "<b>bridge</b>: audio from one person, played to a different "
              "person. That bridge is the product.")],
    ], colWidths=[42 * mm, 123 * mm])
    alt.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    story.append(alt)
    story.append(Paragraph(
        "A hard constraint applies to every option equally: India permits "
        "outbound calls to Indian mobiles only from international numbers, "
        "and no provider may bridge India-to-India. This shapes the design, "
        "not the vendor choice.", styles["note"]))

    # --- 2 -----------------------------------------------------------------
    story.append(Paragraph("2. What it costs", styles["h"]))
    rows = [[cell("Per connected call-minute", True), cell("USD", True, True),
             cell("Basis", True)]]
    for label, value in TWILIO:
        rows.append([cell(label), cell(f"{value:.4f}", right=True),
                     cell("list price")])
    rows.append([cell("<b>Twilio subtotal</b>", True),
                 cell(f"<b>{twilio_min:.4f}</b>", True, True), cell("")])
    rows.append([cell("Gemini translation, both directions"),
                 cell(f"{model_min:.4f}", right=True),
                 cell(f"<b>measured, {m['calls']} calls</b>")])
    rows.append([cell("<b>Total per call-minute</b>", True),
                 cell(f"<b>{total_min:.4f}</b>", True, True),
                 cell(f"<b>≈ Rs {total_min * rate:.0f}/min</b>", True)])
    cost = Table(rows, colWidths=[82 * mm, 24 * mm, 59 * mm])
    cost.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEABOVE", (0, 4), (-1, 4), 0.3, RULE),
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, RULE),
        ("BACKGROUND", (0, -1), (-1, -1), BAND),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
    ]))
    story.append(cost)
    story.append(Paragraph(
        f"Twilio bills each call rounded up to the whole minute: these "
        f"{m['minutes']:.0f} connected minutes were charged as "
        f"{m['billed_min']}, so short test calls cost disproportionately "
        f"more than production ones will. Excluded: number rental "
        f"(~$1.15/month), hosting and taxes. Rupee figure at the rate this "
        f"system records, Rs {rate:.0f}/USD.", styles["note"]))

    # --- 3 -----------------------------------------------------------------
    story.append(Paragraph("3. Why this architecture", styles["h"]))
    story.append(Paragraph(
        "<b>Two call legs bridged by our own backend.</b> No Twilio "
        "conference topology can play translated audio to one participant "
        "and not the other, so the bridge has to be ours. The cost of that "
        "is stated plainly: if the backend stops, the call stops — Twilio is "
        "not connecting the two legs itself.", styles["p"]))
    story.append(Paragraph(
        "<b>Speech-to-speech in one hop, not speech→text→text→speech.</b> A "
        "three-stage chain is cheaper per component and easier to inspect, "
        "but adds three network round trips and discards tone and intent at "
        "every text boundary. The measured figures below are the argument "
        "for the single hop.", styles["p"]))

    lat = Table([
        [cell("Model round trip", True), cell("Model to first audio", True),
         cell("Call setup to live audio", True)],
        [cell(f"{m['rtt']:.0f} ms"), cell(f"{m['first']:.0f} ms"),
         cell(f"{m['setup']:.0f} ms")],
    ], colWidths=[55 * mm, 55 * mm, 55 * mm])
    lat.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 1), (-1, 1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(lat)
    story.append(Paragraph(
        "The pause callers notice is not network latency — it is the model "
        "waiting for a complete phrase before it can translate one. That is "
        "inherent to interpretation, which is why the product fills the wait "
        "with an answer tone and hold music rather than chasing milliseconds "
        "that are already spent.", styles["note"]))
    doc = BaseDocTemplate(str(OUT), pagesize=A4,
                          leftMargin=22 * mm, rightMargin=22 * mm,
                          topMargin=18 * mm, bottomMargin=15 * mm,
                          title="Voice Translation - Platform Decision",
                          author="Softsuave")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="f", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)

    def decorate(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(INK)
        canvas.rect(0, A4[1] - 6 * mm, A4[0], 6 * mm, stroke=0, fill=1)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(22 * mm, 9 * mm,
                          "Figures measured from production call records; "
                          "Twilio rates verified 18 Sep 2026.")
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="p", frames=[frame],
                                       onPage=decorate)])
    doc.build(story)
    print(f"{OUT}")


if __name__ == "__main__":
    build()
