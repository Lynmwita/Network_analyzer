"""
PDF report generator for the AI Integrated Smart Packet Analyzer.

Takes the flows / alerts captured during a run and produces a self-contained
PDF (summary KPIs + pie charts + bar graphs + alert table) using reportlab.
No matplotlib dependency — charts are drawn with reportlab.graphics so the
report renders identically wherever the app runs.
"""

import io
import re
import time
from collections import Counter

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
)
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.barcharts import VerticalBarChart, HorizontalBarChart
from reportlab.graphics.charts.legends import Legend

# ---- Theme -----------------------------------------------------------------
NAVY = colors.HexColor("#0b1220")
TEAL = colors.HexColor("#00b39a")
TEAL_LIGHT = colors.HexColor("#00ffcc")
RED = colors.HexColor("#ef4444")
GREEN = colors.HexColor("#10b981")
BLUE = colors.HexColor("#3b82f6")
AMBER = colors.HexColor("#f59e0b")
GREY = colors.HexColor("#9ca3af")
LIGHT = colors.HexColor("#f3f4f6")
DARK_TEXT = colors.HexColor("#111827")

# A rotating palette for categorical charts (protocols, services, attack types)
PALETTE = [
    colors.HexColor("#3b82f6"), colors.HexColor("#00b39a"), colors.HexColor("#f59e0b"),
    colors.HexColor("#a855f7"), colors.HexColor("#ef4444"), colors.HexColor("#14b8a6"),
    colors.HexColor("#eab308"), colors.HexColor("#ec4899"), colors.HexColor("#64748b"),
    colors.HexColor("#22c55e"),
]

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿️]"
)


def _clean(text):
    """Strip emoji / symbols the core PDF fonts can't render."""
    return _EMOJI_RE.sub("", str(text)).strip()


def _human_bytes(n):
    n = float(n)
    if n < 1024:
        return f"{int(n)} B"
    if n < 1024 ** 2:
        return f"{n/1024:.2f} KB"
    if n < 1024 ** 3:
        return f"{n/1024**2:.2f} MB"
    return f"{n/1024**3:.2f} GB"


# ---- Chart builders --------------------------------------------------------
def _pie(data_pairs, title, width=250, height=170):
    """data_pairs: list of (label, value, color). Returns a Drawing."""
    d = Drawing(width, height)
    d.add(String(width / 2, height - 12, title, fontName="Helvetica-Bold",
                 fontSize=10, fillColor=DARK_TEXT, textAnchor="middle"))

    pairs = [(lbl, val, col) for (lbl, val, col) in data_pairs if val > 0]
    if not pairs:
        d.add(String(width / 2, height / 2, "No data", fontName="Helvetica",
                     fontSize=9, fillColor=GREY, textAnchor="middle"))
        return d

    total = sum(v for _, v, _ in pairs)
    pie = Pie()
    pie.x = 18
    pie.y = 22
    pie.width = 110
    pie.height = 110
    pie.data = [v for _, v, _ in pairs]
    pie.labels = [f"{(v/total)*100:.0f}%" for _, v, _ in pairs]
    pie.slices.strokeColor = colors.white
    pie.slices.strokeWidth = 1
    pie.sideLabels = False
    pie.simpleLabels = True
    for i, (_, _, col) in enumerate(pairs):
        pie.slices[i].fillColor = col
    d.add(pie)

    legend = Legend()
    legend.x = 140
    legend.y = height - 34
    legend.dx = 8
    legend.dy = 8
    legend.fontName = "Helvetica"
    legend.fontSize = 8
    legend.boxAnchor = "nw"
    legend.columnMaximum = 8
    legend.alignment = "right"
    legend.dxTextSpace = 5
    legend.deltay = 12
    legend.colorNamePairs = [
        (col, f"{_clean(lbl)} ({val})") for lbl, val, col in pairs
    ]
    d.add(legend)
    return d


def _vbar(labels, values, title, color=BLUE, width=250, height=185):
    d = Drawing(width, height)
    d.add(String(width / 2, height - 12, title, fontName="Helvetica-Bold",
                 fontSize=10, fillColor=DARK_TEXT, textAnchor="middle"))
    if not values or sum(values) == 0:
        d.add(String(width / 2, height / 2, "No data", fontName="Helvetica",
                     fontSize=9, fillColor=GREY, textAnchor="middle"))
        return d

    chart = VerticalBarChart()
    chart.x = 30
    chart.y = 38
    chart.width = width - 55
    chart.height = height - 70
    chart.data = [values]
    chart.categoryAxis.categoryNames = [_clean(l)[:10] for l in labels]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7
    chart.categoryAxis.labels.angle = 30
    chart.categoryAxis.labels.boxAnchor = "ne"
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.barWidth = 6
    chart.groupSpacing = 6
    chart.bars[0].fillColor = color
    chart.bars[0].strokeColor = colors.white
    d.add(chart)
    return d


def _hbar(labels, values, title, colors_list=None, width=250, height=185):
    d = Drawing(width, height)
    d.add(String(width / 2, height - 12, title, fontName="Helvetica-Bold",
                 fontSize=10, fillColor=DARK_TEXT, textAnchor="middle"))
    if not values or sum(values) == 0:
        d.add(String(width / 2, height / 2, "No data", fontName="Helvetica",
                     fontSize=9, fillColor=GREY, textAnchor="middle"))
        return d

    chart = HorizontalBarChart()
    chart.x = 95
    chart.y = 20
    chart.width = width - 115
    chart.height = height - 45
    chart.data = [values]
    chart.categoryAxis.categoryNames = [_clean(l)[:16] for l in labels]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.barWidth = 9
    chart.groupSpacing = 5
    chart.bars[0].fillColor = colors_list[0] if colors_list else TEAL
    chart.bars[0].strokeColor = colors.white
    d.add(chart)
    return d


# ---- Main entry ------------------------------------------------------------
def build_report(flows, alerts, total_bytes, mode="Analysis", threshold=0.75):
    """
    Build a PDF report from a run and return raw PDF bytes.

    flows / alerts: list of flow-info dicts (as produced by build_flow_info in app.py).
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title="Smart Packet Analyzer Report",
    )

    styles = getSampleStyleSheet()
    h_title = ParagraphStyle("h_title", parent=styles["Title"], textColor=TEAL,
                             fontName="Helvetica-Bold", fontSize=20, spaceAfter=2)
    h_sub = ParagraphStyle("h_sub", parent=styles["Normal"], textColor=GREY,
                           fontSize=9, alignment=TA_CENTER, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=DARK_TEXT,
                        fontName="Helvetica-Bold", fontSize=13, spaceBefore=10, spaceAfter=6)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9,
                          textColor=DARK_TEXT, leading=13)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=GREY)

    total_flows = len(flows)
    total_alerts = len(alerts)
    ratio = (total_alerts / total_flows * 100) if total_flows else 0.0

    story = []

    # ---- Header banner ----
    banner = Table(
        [[Paragraph("&nbsp;", body)],
         [Paragraph("AI INTEGRATED SMART PACKET ANALYZER", h_title)],
         [Paragraph("Network Intrusion Detection — Session Report", h_sub)],
         [Paragraph("&nbsp;", small)]],
        colWidths=[doc.width],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    story.append(banner)
    story.append(Spacer(1, 6))

    meta_tbl = Table([[
        Paragraph(f"<b>Analysis mode:</b> {_clean(mode)}", small),
        Paragraph(f"<b>Alert threshold:</b> {threshold*100:.0f}%", small),
        Paragraph(f"<b>Generated:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}", small),
    ]], colWidths=[doc.width * 0.4, doc.width * 0.25, doc.width * 0.35])
    meta_tbl.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, GREY),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 10))

    # ---- KPI cards ----
    verdict = "THREATS DETECTED" if total_alerts else "NO THREATS DETECTED"
    verdict_color = RED if total_alerts else GREEN
    kpi_data = [[
        _kpi_cell("ANALYZED FLOWS", str(total_flows), BLUE),
        _kpi_cell("SECURITY ALERTS", str(total_alerts), RED if total_alerts else GREEN),
        _kpi_cell("INTRUSION RATIO", f"{ratio:.2f}%", RED if ratio > 5 else GREEN),
        _kpi_cell("TOTAL BANDWIDTH", _human_bytes(total_bytes), BLUE),
    ]]
    kpi = Table(kpi_data, colWidths=[doc.width / 4.0] * 4)
    kpi.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(kpi)
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"<b>Overall verdict:</b> <font color='{verdict_color.hexval()}'>"
        f"<b>{verdict}</b></font> &nbsp;—&nbsp; {total_alerts} of {total_flows} "
        f"analyzed flows were classified as malicious at or above the "
        f"{threshold*100:.0f}% confidence threshold.", body))
    story.append(Spacer(1, 8))

    # ---- Row 1 of charts: threat pie + protocol pie ----
    normal_ct = sum(1 for f in flows if f.get("raw_label") != 1 or f not in alerts)
    attack_ct = total_alerts
    normal_ct = total_flows - attack_ct
    threat_pie = _pie(
        [("Normal", normal_ct, GREEN), ("Attack", attack_ct, RED)],
        "Threat Classification", width=doc.width / 2 - 6)

    proto_counts = Counter(_clean(f.get("protocol", "?")) for f in flows)
    proto_pairs = [(k, v, PALETTE[i % len(PALETTE)])
                   for i, (k, v) in enumerate(proto_counts.most_common(6))]
    proto_pie = _pie(proto_pairs, "Protocol Distribution", width=doc.width / 2 - 6)

    story.append(Paragraph("Traffic Overview", h2))
    row1 = Table([[threat_pie, proto_pie]], colWidths=[doc.width / 2, doc.width / 2])
    row1.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(row1)

    # ---- Row 2: top services bar + attack category bar ----
    svc_counts = Counter(_clean(f.get("service", "-")) or "-" for f in flows)
    svc_top = svc_counts.most_common(8)
    svc_bar = _vbar([k for k, _ in svc_top], [v for _, v in svc_top],
                    "Top Services", color=BLUE, width=doc.width / 2 - 6)

    atk_counts = Counter(_clean(a.get("attack_type", "-")) or "Unknown" for a in alerts)
    atk_top = atk_counts.most_common(8)
    if atk_top:
        atk_bar = _hbar([k for k, _ in atk_top], [v for _, v in atk_top],
                        "Attack Categories", colors_list=[RED], width=doc.width / 2 - 6)
    else:
        atk_bar = _pie([], "Attack Categories", width=doc.width / 2 - 6)

    story.append(Spacer(1, 4))
    row2 = Table([[svc_bar, atk_bar]], colWidths=[doc.width / 2, doc.width / 2])
    row2.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(row2)

    # ---- Severity breakdown (only if alerts) ----
    if alerts:
        sev_counts = Counter(_clean(a.get("severity", "")) or "Unrated" for a in alerts)
        order = ["HIGH", "MEDIUM", "LOW"]
        sev_labels, sev_vals, sev_cols = [], [], []
        colmap = {"HIGH": RED, "MEDIUM": AMBER, "LOW": GREEN}
        for key in order:
            for lbl, v in sev_counts.items():
                if key in lbl.upper():
                    sev_labels.append(key.title())
                    sev_vals.append(v)
                    sev_cols.append(colmap[key])
        if sev_vals:
            story.append(Paragraph("Alert Severity Breakdown", h2))
            sev_pie = _pie(list(zip(sev_labels, sev_vals, sev_cols)),
                           "Severity", width=doc.width / 2 - 6)
            sev_bar = _vbar(sev_labels, sev_vals, "Severity Counts",
                            color=RED, width=doc.width / 2 - 6)
            row3 = Table([[sev_pie, sev_bar]], colWidths=[doc.width / 2, doc.width / 2])
            row3.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.append(row3)

    # ---- Alerts table ----
    story.append(Paragraph("Security Alerts", h2))
    if alerts:
        header = ["#", "Source", "Destination", "Proto", "Service", "Attack Type", "Sev", "Conf"]
        rows = [header]
        for i, a in enumerate(alerts[:40], 1):
            rows.append([
                str(i),
                f"{a.get('src_ip','')}:{a.get('src_port','')}",
                f"{a.get('dst_ip','')}:{a.get('dst_port','')}",
                _clean(a.get("protocol", "")),
                _clean(a.get("service", "")),
                _clean(a.get("attack_type", "-")),
                _clean(a.get("severity", "-")),
                str(a.get("confidence", "")),
            ])
        col_w = [8*mm, 33*mm, 33*mm, 13*mm, 18*mm, 27*mm, 16*mm, 14*mm]
        tbl = Table(rows, colWidths=col_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ("TEXTCOLOR", (0, 1), (-1, -1), DARK_TEXT),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(tbl)
        if len(alerts) > 40:
            story.append(Spacer(1, 3))
            story.append(Paragraph(f"… and {len(alerts) - 40} more alerts "
                                   f"(full list available via CSV export).", small))
    else:
        story.append(Paragraph("No malicious flows were detected in this session. "
                               "All analyzed traffic was classified as benign.", body))

    # ---- Footer note ----
    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "Generated by the AI Integrated Smart Packet Analyzer — XGBoost classifier "
        "trained on the UNSW-NB15 intrusion-detection dataset. This report reflects "
        "model predictions on captured network flows and is intended for security "
        "analysis and academic purposes.", small))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def _kpi_cell(label, value, value_color):
    """A single KPI 'card' rendered as a nested table."""
    styles = getSampleStyleSheet()
    v_style = ParagraphStyle("kpi_v", parent=styles["Normal"], fontSize=17,
                             fontName="Helvetica-Bold", textColor=value_color,
                             alignment=TA_CENTER, leading=19)
    l_style = ParagraphStyle("kpi_l", parent=styles["Normal"], fontSize=7,
                             textColor=GREY, alignment=TA_CENTER, leading=9)
    inner = Table([[Paragraph(value, v_style)], [Paragraph(label, l_style)]])
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f9fafb")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#e5e7eb")),
        ("ROUNDEDCORNERS", [5, 5, 5, 5]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return inner
