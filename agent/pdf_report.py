"""
PDF Report Builder
--------------------
Assembles GitHub Intelligence + Website Intelligence + AI Competitive
Intelligence into a single professionally styled, downloadable PDF:
cover page, Data Sources checklist, executive summary, website analysis,
GitHub analysis (or Developer Ecosystem fallback if unavailable), market
intelligence, customer sentiment, technology stack, SWOT analysis, AI
insights, recommendations, opportunities, predictions, and conclusion —
each major section carrying a confidence score.

Uses reportlab (Platypus) for layout and matplotlib for chart images,
per the project's pdf skill guidance.
"""

import os
import io
import requests
import matplotlib
matplotlib.use("Agg")  # headless — no display server on the server
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image, Table, TableStyle,
    HRFlowable,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

NAVY = colors.HexColor("#1e293b")
ACCENT = colors.HexColor("#6366f1")
LIGHT_GREY = colors.HexColor("#f1f5f9")
GOOD = colors.HexColor("#16a34a")
WARN = colors.HexColor("#d97706")
BAD = colors.HexColor("#dc2626")

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generated_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CoverTitle", fontSize=28, leading=34, textColor=NAVY,
                               alignment=TA_CENTER, spaceAfter=6, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="CoverSubtitle", fontSize=14, leading=18, textColor=ACCENT,
                               alignment=TA_CENTER, spaceAfter=4, fontName="Helvetica"))
    styles.add(ParagraphStyle(name="CoverMeta", fontSize=10, leading=14, textColor=colors.HexColor("#64748b"),
                               alignment=TA_CENTER, fontName="Helvetica"))
    styles.add(ParagraphStyle(name="SectionHeading", fontSize=16, leading=20, textColor=NAVY,
                               spaceBefore=14, spaceAfter=8, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="SubHeading", fontSize=12, leading=16, textColor=ACCENT,
                               spaceBefore=10, spaceAfter=4, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="BodyTextSmall", fontSize=9.5, leading=14, textColor=colors.HexColor("#334155"),
                               alignment=TA_LEFT, fontName="Helvetica"))
    styles.add(ParagraphStyle(name="BulletSmall", fontSize=9.5, leading=14, leftIndent=12,
                               textColor=colors.HexColor("#334155"), fontName="Helvetica"))
    return styles


def _p(text, style):
    """Escape/guard against None or non-string content before building a Paragraph."""
    if text is None:
        text = ""
    text = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(text, style)


def _p_markup(text, style):
    """Like _p but allows a small set of our own <b> tags through (already-safe strings)."""
    if text is None:
        text = ""
    return Paragraph(str(text), style)


def _bullets(items, style, empty_text=None):
    if empty_text is None:
        empty_text = "Detailed information for this item was not publicly available at the time of analysis."
    if not items:
        return [_p(f"• {empty_text}", style)]
    return [_p(f"• {item}", style) for item in items]


def _confidence_label(score):
    """Renders a confidence value as 'NN%' or 'Not Available' for None/github-missing sections."""
    if score is None:
        return "Not Available"
    return f"{int(round(score))}%"


def _confidence_color(score):
    if score is None:
        return colors.HexColor("#94a3b8")
    if score >= 70:
        return GOOD
    if score >= 40:
        return WARN
    return BAD


def _section_heading_with_confidence(title, confidence_score, styles):
    """A SectionHeading paragraph with a right-aligned confidence badge on the same row."""
    label = _confidence_label(confidence_score)
    color = _confidence_color(confidence_score)
    conf_style = ParagraphStyle(
        name=f"ConfBadge_{title}_{label}", fontSize=11, fontName="Helvetica-Bold",
        textColor=color, alignment=TA_CENTER,
    )
    row = Table(
        [[_p(title, styles["SectionHeading"]), _p(label, conf_style)]],
        colWidths=[10.5 * cm, 2.5 * cm],
    )
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    return row


def _data_sources_section(ai_intel: dict, styles):
    """
    Renders the ✓ / ✗ Data Sources checklist near the beginning of the
    report, plus the Overall Confidence score — so the reader immediately
    knows what this report is (and isn't) grounded in.
    """
    story = [_p("Data Sources", styles["SectionHeading"])]
    data_sources = (ai_intel.get("data_sources") or {}).get("order", [])
    confidence = ai_intel.get("confidence") or {}

    rows = []
    for label, used in data_sources:
        mark = "✓" if used else "✗"
        rows.append([mark, label])

    if rows:
        table = Table(rows, colWidths=[1.2 * cm, 8 * cm])
        style_cmds = [
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for i, (label, used) in enumerate(data_sources):
            style_cmds.append(("TEXTCOLOR", (0, i), (0, i), GOOD if used else BAD))
        table.setStyle(TableStyle(style_cmds))
        story.append(table)
    story.append(Spacer(1, 0.3 * cm))

    overall = confidence.get("overall")
    overall_style = ParagraphStyle(
        name="OverallConfidence", fontSize=12, fontName="Helvetica-Bold",
        textColor=_confidence_color(overall),
    )
    story.append(_p(f"Overall Confidence: {_confidence_label(overall)}", overall_style))
    story.append(PageBreak())
    return story


# ---------------------------------------------------------------------------
# Chart builders (matplotlib -> in-memory PNG -> reportlab Image)
# ---------------------------------------------------------------------------

def _chart_repo_stats(github_intel: dict):
    stats = {
        "Stars": github_intel.get("stars") or 0,
        "Forks": github_intel.get("forks") or 0,
        "Watchers": github_intel.get("watchers") or 0,
    }
    fig, ax = plt.subplots(figsize=(5.2, 2.6), dpi=150)
    bars = ax.bar(list(stats.keys()), list(stats.values()), color=["#6366f1", "#0f766e", "#f59e0b"])
    ax.set_title("Repository Metrics", fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    for b in bars:
        h = b.get_height()
        ax.annotate(f"{int(h):,}", (b.get_x() + b.get_width() / 2, h),
                    ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=13 * cm, height=6.5 * cm)


def _chart_languages(languages: dict):
    if not languages:
        return None
    top = dict(sorted(languages.items(), key=lambda x: x[1], reverse=True)[:6])
    fig, ax = plt.subplots(figsize=(5.2, 3.2), dpi=150)
    ax.pie(list(top.values()), labels=[f"{k} ({v}%)" for k, v in top.items()],
           autopct=None, startangle=90,
           colors=["#6366f1", "#0f766e", "#f59e0b", "#ef4444", "#8b5cf6", "#22c55e"])
    ax.set_title("Language Breakdown", fontsize=11)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=11 * cm, height=6.5 * cm)


def _chart_repo_growth_by_year(growth: dict):
    by_year = (growth or {}).get("public_repos_created_by_year") or {}
    if not by_year:
        return None
    fig, ax = plt.subplots(figsize=(5.2, 2.6), dpi=150)
    ax.bar(list(by_year.keys()), list(by_year.values()), color="#6366f1")
    ax.set_title("Public Repos Created by Year (Org)", fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=13 * cm, height=6.5 * cm)


def _score_gauge(label: str, score, reasoning: str, styles):
    """Simple horizontal bar 'gauge' for a 0-100 score, drawn as a small table."""
    score = score if isinstance(score, (int, float)) else 0
    color = GOOD if score >= 70 else WARN if score >= 40 else BAD
    filled_width = max(0.02, min(1.0, score / 100)) * 10 * cm
    inner = Table([[""]], colWidths=[filled_width], rowHeights=[0.5 * cm])
    inner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color)]))

    outer = Table(
        [[_p_markup(f"<b>{label}</b>: {score}/100", styles["BodyTextSmall"])],
         [inner],
         [_p(reasoning or "", styles["BulletSmall"])]],
        colWidths=[13 * cm],
    )
    outer.setStyle(TableStyle([
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    return outer


def _fetch_logo_image(logo_url: str):
    if not logo_url:
        return None
    try:
        resp = requests.get(logo_url, timeout=8)
        if resp.status_code == 200 and resp.content:
            buf = io.BytesIO(resp.content)
            return Image(buf, width=3 * cm, height=3 * cm)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _cover_page(competitor: dict, styles):
    from datetime import datetime
    story = []
    story.append(Spacer(1, 3 * cm))
    logo_img = _fetch_logo_image(competitor.get("logo_url"))
    if logo_img:
        logo_img.hAlign = "CENTER"
        story.append(logo_img)
        story.append(Spacer(1, 0.6 * cm))
    story.append(_p("SpyLens Competitive Intelligence Report", styles["CoverTitle"]))
    story.append(_p(competitor.get("name", "Unknown Company"), styles["CoverSubtitle"]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(HRFlowable(width="40%", thickness=1, color=ACCENT, hAlign="CENTER"))
    story.append(Spacer(1, 1.5 * cm))
    website = competitor.get("website_url") or "Not discovered"
    github = competitor.get("github_repo") or "Not discovered"
    story.append(_p(f"Website: {website}", styles["CoverMeta"]))
    story.append(_p(f"GitHub: {github}", styles["CoverMeta"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_p(f"Generated {datetime.now().strftime('%d %B %Y, %H:%M')}", styles["CoverMeta"]))
    story.append(_p("Prepared by SpyLens — Competitor Intelligence for Indian Startups", styles["CoverMeta"]))
    story.append(PageBreak())
    return story


def _executive_summary_section(ai_intel: dict, styles):
    story = [_p("Executive Summary", styles["SectionHeading"])]
    if ai_intel.get("ai_generation_failed"):
        story.append(_p(ai_intel.get("ai_unavailable_message") or
                         "AI analysis is temporarily unavailable. Rule-based intelligence has been used for this section.",
                         styles["BulletSmall"]))
        story.append(Spacer(1, 0.15 * cm))
    story.append(_p(ai_intel.get("executive_summary") or
                     "A summary could not be generated from available data at this time.", styles["BodyTextSmall"]))
    story.append(Spacer(1, 0.4 * cm))
    innovation = ai_intel.get("innovation_score") or {}
    overall = ai_intel.get("overall_rating") or {}
    story.append(_score_gauge("Innovation Score", innovation.get("score"), innovation.get("reasoning"), styles))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_score_gauge("Overall Rating", overall.get("score"), overall.get("reasoning"), styles))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_p_markup("<b>Business Overview</b>", styles["SubHeading"]))
    story.append(_p(ai_intel.get("business_overview") or
                     "Business model details could not be independently verified from public sources.", styles["BodyTextSmall"]))
    story.append(_p_markup("<b>Product Direction</b>", styles["SubHeading"]))
    story.append(_p(ai_intel.get("product_direction") or
                     "Product direction could not be independently verified from public sources.", styles["BodyTextSmall"]))
    story.append(PageBreak())
    return story


def _website_analysis_section(website_intel: dict, ai_intel: dict, styles):
    confidence = (ai_intel.get("confidence") or {}).get("website_analysis")
    story = [_section_heading_with_confidence("Website Analysis", confidence, styles)]

    if not website_intel.get("available"):
        story.append(_p(
            website_intel.get("reason") or
            "No official website could be verified for this company through public discovery. "
            "The remainder of this report relies on GitHub, news and community sources instead.",
            styles["BodyTextSmall"]
        ))
        story.append(PageBreak())
        return story

    homepage = website_intel.get("homepage") or {}
    meta = website_intel.get("meta_information") or {}

    story.append(_p_markup("<b>Homepage</b>", styles["SubHeading"]))
    story.append(_p(f"Title: {homepage.get('title', 'N/A')}", styles["BodyTextSmall"]))
    story.append(_p(f"Meta Description: {meta.get('meta_description') or homepage.get('meta_description') or 'N/A'}", styles["BodyTextSmall"]))
    story.append(Spacer(1, 0.2 * cm))

    rows = [["Section", "Status", "Details"]]
    for label, key in [("Products", "products"), ("Features", "features"), ("Pricing", "pricing"),
                       ("Blog", "blog"), ("Documentation", "documentation"), ("Careers", "careers")]:
        info = website_intel.get(key) or {}
        status = "Found" if info.get("status") == "found" else "Not Found"
        details = (info.get("title") or info.get("url") or "-")[:60]
        rows.append([label, status, details])

    table = Table(rows, colWidths=[3.5 * cm, 2.5 * cm, 7 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.3 * cm))

    if website_intel.get("latest_updates"):
        story.append(_p_markup("<b>Latest Updates (from Blog)</b>", styles["SubHeading"]))
        story.extend(_bullets(website_intel["latest_updates"], styles["BulletSmall"]))
        story.append(Spacer(1, 0.2 * cm))

    story.append(_p_markup("<b>AI Summary</b>", styles["SubHeading"]))
    story.append(_p(website_intel.get("ai_summary") or
                     "A narrative summary could not be generated for this section at this time.",
                     styles["BodyTextSmall"]))
    story.append(PageBreak())
    return story


def _github_analysis_section(github_intel: dict, ai_intel: dict, styles):
    confidence = (ai_intel.get("confidence") or {}).get("github_analysis")
    story = [_section_heading_with_confidence("GitHub Analysis", confidence, styles)]

    if not github_intel.get("available"):
        # Never show a bare "no data" line — render a full Developer Ecosystem
        # explanation instead, continuing the analysis from other sources.
        story.append(_p_markup("<b>Developer Ecosystem</b>", styles["SubHeading"]))
        explanation = ai_intel.get("no_github_explanation") or (
            "No verified public GitHub repository was found. This company may use "
            "private repositories or closed-source development."
        )
        story.append(_p(explanation, styles["BodyTextSmall"]))
        story.append(Spacer(1, 0.15 * cm))
        story.append(_p(ai_intel.get("public_source_disclaimer") or
                         "Since no verified public GitHub repository was available, the following "
                         "insights are based on public customer reviews, industry trends, community "
                         "discussions and official company information.",
                         styles["BodyTextSmall"]))
        story.append(Spacer(1, 0.2 * cm))
        story.append(_p(ai_intel.get("developer_ecosystem") or ai_intel.get("developer_activity") or
                         "Engineering activity for this company could not be assessed through public "
                         "channels; the analysis instead draws on the company's website, news coverage "
                         "and community discussions covered elsewhere in this report.",
                         styles["BodyTextSmall"]))
        story.append(PageBreak())
        return story

    story.append(_p(f"Tracked Repository: {github_intel.get('tracked_repo', 'N/A')}", styles["BodyTextSmall"]))
    story.append(Spacer(1, 0.2 * cm))

    chart = _chart_repo_stats(github_intel)
    chart.hAlign = "CENTER"
    story.append(chart)
    story.append(Spacer(1, 0.3 * cm))

    lang_chart = _chart_languages(github_intel.get("languages") or {})
    if lang_chart:
        lang_chart.hAlign = "CENTER"
        story.append(lang_chart)
        story.append(Spacer(1, 0.3 * cm))

    commit_activity = github_intel.get("commit_activity") or {}
    release_freq = github_intel.get("release_frequency") or {}
    rows = [
        ["Metric", "Value"],
        ["Open Issues", str(github_intel.get("open_issues", "N/A"))],
        ["Commit Activity", str(commit_activity.get("commits_last_4_weeks",
                                                      commit_activity.get("commits_last_7_days", "N/A")))],
        ["Total Releases Seen", str(release_freq.get("total_releases_seen", "N/A"))],
        ["Avg Days Between Releases", str(release_freq.get("avg_days_between_releases", "N/A"))],
        ["Contributors Tracked", str(len(github_intel.get("contributors", [])))],
        ["Most Active Repository", (github_intel.get("most_active_repository") or {}).get("full_name", "N/A")],
    ]
    table = Table(rows, colWidths=[6 * cm, 7 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.3 * cm))

    growth_chart = _chart_repo_growth_by_year(github_intel.get("repository_growth"))
    if growth_chart:
        growth_chart.hAlign = "CENTER"
        story.append(growth_chart)
        story.append(Spacer(1, 0.3 * cm))

    story.append(_p_markup("<b>AI Summary</b>", styles["SubHeading"]))
    story.append(_p(github_intel.get("ai_summary") or
                     "A narrative summary could not be generated for this section at this time.",
                     styles["BodyTextSmall"]))
    story.append(PageBreak())
    return story


def _tech_stack_section(github_intel: dict, website_intel: dict, styles):
    story = [_p("Technology Stack", styles["SectionHeading"])]
    frameworks = github_intel.get("frameworks", []) if github_intel.get("available") else []
    tech_stack = website_intel.get("tech_stack", []) if website_intel.get("available") else []
    languages = list((github_intel.get("languages") or {}).keys()) if github_intel.get("available") else []

    story.append(_p_markup("<b>Detected Languages</b>", styles["SubHeading"]))
    story.extend(_bullets(languages, styles["BulletSmall"],
                           "No GitHub-verified languages were available; see Website Tech Fingerprint below."))
    story.append(_p_markup("<b>Frameworks / Topics (GitHub)</b>", styles["SubHeading"]))
    story.extend(_bullets(frameworks, styles["BulletSmall"],
                           "No frameworks could be inferred from public repository topics."))
    story.append(_p_markup("<b>Website Tech Fingerprint</b>", styles["SubHeading"]))
    story.extend(_bullets(tech_stack, styles["BulletSmall"],
                           "No recognizable web technology signatures were detected on the homepage."))
    story.append(PageBreak())
    return story


def _market_customer_section(ai_intel: dict, styles):
    """
    Market Position + Customer Sentiment — always populated (AI-generated
    when available, rule-based otherwise), grounded in news/community
    signals per the fallback priority order.
    """
    market_confidence = (ai_intel.get("confidence") or {}).get("market_intelligence")
    sentiment_confidence = (ai_intel.get("confidence") or {}).get("customer_sentiment")

    story = [_section_heading_with_confidence("Market Intelligence", market_confidence, styles)]
    story.append(_p(ai_intel.get("market_position") or
                     "Market position could not be independently verified from public sources.",
                     styles["BodyTextSmall"]))
    story.append(Spacer(1, 0.3 * cm))

    story.append(_section_heading_with_confidence("Customer Sentiment", sentiment_confidence, styles))
    story.append(_p(ai_intel.get("customer_sentiment") or
                     "Customer sentiment could not be independently verified from public sources.",
                     styles["BodyTextSmall"]))
    story.append(PageBreak())
    return story


_SWOT_HEAD_STYLE_CACHE = {}


def _swot_head_style(color):
    key = str(color)
    if key not in _SWOT_HEAD_STYLE_CACHE:
        _SWOT_HEAD_STYLE_CACHE[key] = ParagraphStyle(
            name=f"SwotHead{key}", fontSize=11, textColor=color,
            fontName="Helvetica-Bold", spaceAfter=4
        )
    return _SWOT_HEAD_STYLE_CACHE[key]


def _swot_section(ai_intel: dict, styles):
    story = [_p("SWOT Analysis", styles["SectionHeading"])]
    swot = ai_intel.get("swot") or {}

    def block(title, items, color):
        cell_story = [_p(title, _swot_head_style(color))]
        default_item = "No specific item could be verified from public data for this quadrant."
        for item in (items or [default_item]):
            cell_story.append(_p(f"• {item}", styles["BulletSmall"]))
        return cell_story

    table_data = [
        [block("Strengths", swot.get("strengths"), GOOD), block("Weaknesses", swot.get("weaknesses"), BAD)],
        [block("Opportunities", swot.get("opportunities"), ACCENT), block("Threats", swot.get("threats"), WARN)],
    ]
    t = Table(table_data, colWidths=[6.5 * cm, 6.5 * cm], rowHeights=[4.5 * cm, 4.5 * cm])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.75, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#f0fdf4")),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#fef2f2")),
        ("BACKGROUND", (0, 1), (0, 1), colors.HexColor("#eef2ff")),
        ("BACKGROUND", (1, 1), (1, 1), colors.HexColor("#fffbeb")),
    ]))
    story.append(t)
    story.append(PageBreak())
    return story


def _insights_and_recommendations_section(ai_intel: dict, styles):
    predictions_confidence = (ai_intel.get("confidence") or {}).get("predictions")

    story = [_p("AI Insights & Recommendations", styles["SectionHeading"])]

    story.append(_p_markup("<b>Developer Ecosystem</b>", styles["SubHeading"]))
    story.append(_p(ai_intel.get("developer_ecosystem") or ai_intel.get("developer_activity") or
                     "Developer/engineering activity could not be independently verified from public sources.",
                     styles["BodyTextSmall"]))

    story.append(_p_markup("<b>Hiring Trends</b>", styles["SubHeading"]))
    story.append(_p(ai_intel.get("hiring_trends") or
                     "Hiring activity could not be independently verified from public sources.",
                     styles["BodyTextSmall"]))

    story.append(_p_markup("<b>Recommendations</b>", styles["SubHeading"]))
    story.extend(_bullets(ai_intel.get("recommendations"), styles["BulletSmall"],
                           "Specific recommendations could not be generated from the data currently available."))

    story.append(_p_markup("<b>Opportunities</b>", styles["SubHeading"]))
    story.extend(_bullets((ai_intel.get("swot") or {}).get("opportunities"), styles["BulletSmall"],
                           "No specific opportunities could be identified from the data currently available."))

    story.append(_section_heading_with_confidence("Future Predictions", predictions_confidence, styles))
    story.extend(_bullets(ai_intel.get("future_predictions"), styles["BulletSmall"],
                           "Predictions could not be generated from the data currently available."))

    story.append(PageBreak())
    return story


def _conclusion_section(competitor: dict, ai_intel: dict, styles):
    story = [_p("Conclusion", styles["SectionHeading"])]
    overall = (ai_intel.get("overall_rating") or {}).get("score")
    rating_text = f"{overall}/100" if overall is not None else "not available"
    overall_confidence = (ai_intel.get("confidence") or {}).get("overall")
    name = str(competitor.get("name", "this company")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    github_available = ai_intel.get("github_available", False)
    source_phrase = "website, GitHub, news and community" if github_available else "website, news and community"

    story.append(_p_markup(
        f"Based on the combined {source_phrase} intelligence gathered for "
        f"<b>{name}</b>, SpyLens rates their "
        f"overall competitive position at <b>{rating_text}</b>, with an overall "
        f"confidence of <b>{_confidence_label(overall_confidence)}</b> in the underlying data. "
        f"This report should be treated as a directional signal to guide further "
        f"manual due diligence — not a substitute for it.",
        styles["BodyTextSmall"]
    ))
    if not github_available:
        story.append(Spacer(1, 0.2 * cm))
        story.append(_p(
            ai_intel.get("no_github_explanation") or
            "No verified public GitHub repository was found. This company may use "
            "private repositories or closed-source development.",
            styles["BodyTextSmall"]
        ))
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1")))
    story.append(Spacer(1, 0.2 * cm))
    story.append(_p("Generated automatically by SpyLens. Data is sourced from public "
                     "GitHub APIs, the company's official website, public news and public "
                     "community discussions; accuracy depends on what is publicly available "
                     "at generation time.",
                     styles["CoverMeta"]))
    return story


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def generate_pdf_report(competitor: dict, github_intel: dict, website_intel: dict, ai_intel: dict) -> str:
    """
    Builds the full PDF and returns the absolute file path.
    """
    styles = _styles()
    safe_name = "".join(c if c.isalnum() else "_" for c in competitor.get("name", "company")).strip("_") or "company"
    filename = f"spylens_report_{safe_name}_{competitor.get('id', 'x')}.pdf"
    filepath = os.path.join(REPORTS_DIR, filename)

    doc = SimpleDocTemplate(
        filepath, pagesize=A4,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
        title=f"SpyLens Report — {competitor.get('name', '')}",
    )

    story = []
    story.extend(_cover_page(competitor, styles))
    story.extend(_data_sources_section(ai_intel, styles))
    story.extend(_executive_summary_section(ai_intel, styles))
    story.extend(_website_analysis_section(website_intel, ai_intel, styles))
    story.extend(_github_analysis_section(github_intel, ai_intel, styles))
    story.extend(_market_customer_section(ai_intel, styles))
    story.extend(_tech_stack_section(github_intel, website_intel, styles))
    story.extend(_swot_section(ai_intel, styles))
    story.extend(_insights_and_recommendations_section(ai_intel, styles))
    story.extend(_conclusion_section(competitor, ai_intel, styles))

    doc.build(story)
    return filepath