"""Deterministic Threads-sized news card renderer."""

from datetime import datetime
import os

from PIL import Image, ImageDraw, ImageFont


SIZE = 1080


COLOR_CARD_VARIANTS = {
    "black": {"background": "#111111", "text": "#FFFFFF", "muted": "#D1D5DB", "rule": "#4B5563"},
    "red": {"background": "#B42318", "text": "#FFFFFF", "muted": "#FEE4E2", "rule": "#FDA29B"},
    "teal": {"background": "#073B4C", "text": "#F7F4EC", "muted": "#CFE4E6", "rule": "#4D7D86"},
}


def card_style(name="classic", color_variant="black"):
    styles = {
        "classic": {
            "background": "#F7F4EC", "text": "#11233D", "accent": "#D64545",
            "muted": "#667085", "rule": "#D0D5DD", "texture": "none",
            "bold_fonts": ("TaipeiSansTCBeta-Bold.ttf", "msjhbd.ttc", "msjh.ttc"),
            "regular_fonts": ("TaipeiSansTCBeta-Regular.ttf", "TaipeiSansTCBeta-Bold.ttf", "msjh.ttc"),
            "point_distribution": "even", "point_area_top": 500, "point_area_bottom": 810,
        },
        "round": {
            "background": "#EAF5F1", "text": "#153C36", "accent": "#E56B5D",
            "muted": "#5E766F", "rule": "#BFD8D1", "texture": "dots",
            "bold_fonts": ("wt009.ttf", "msjhbd.ttc", "TaipeiSansTCBeta-Bold.ttf"),
            "regular_fonts": ("jf-openhuninn-2.1.ttf", "msjh.ttc", "TaipeiSansTCBeta-Regular.ttf"),
            "accent": "#1F6B5A", "marker_shape": "circle", "point_distribution": "even", "point_area_top": 500, "point_area_bottom": 810,
        },
        "song": {
            "background": "#E7EBF0", "text": "#24354B", "accent": "#5B7086",
            "muted": "#627181", "rule": "#B9C6D2", "texture": "none", "blocks": "three", "point_distribution": "even", "point_area_top": 500, "point_area_bottom": 810, "middle_block": "#FFFFFF", "middle_block_top": 420, "middle_block_bottom": 880,
            "bold_fonts": ("wt004.ttf", "AdobeSongStd-Light.otf"),
            "regular_fonts": ("AdobeSongStd-Light.otf",),
        },
        "colorcard": {
            "background": "#111111", "text": "#FFFFFF", "accent": "#FFFFFF",
            "muted": "#D1D5DB", "rule": "#4B5563", "texture": "none",
            "bold_fonts": ("wt009.ttf", "msjhbd.ttc", "TaipeiSansTCBeta-Bold.ttf"),
            "regular_fonts": ("jf-openhuninn-2.1.ttf", "msjh.ttc", "TaipeiSansTCBeta-Regular.ttf"),
            "point_distribution": "even", "point_area_top": 500, "point_area_bottom": 810,
            "full_bleed": True, "marker_shape": "square",
        },
        "image_title": {
            "background": "#11233D", "text": "#FFFFFF", "accent": "#D64545",
            "muted": "#B8C7D6", "rule": "#38506B", "texture": "none",
            "bold_fonts": ("wt009.ttf", "msjhbd.ttc", "TaipeiSansTCBeta-Bold.ttf"),
            "regular_fonts": ("jf-openhuninn-2.1.ttf", "msjh.ttc", "TaipeiSansTCBeta-Regular.ttf"),
            "point_distribution": "even", "point_area_top": 790, "point_area_bottom": 990,
            "full_bleed": True, "marker_shape": "square", "image_title": True,
        },
    }
    if name not in styles:
        raise ValueError("不支援的圖卡風格")
    style = {
        "headline_size": 208,
        "point_size": 88,
        "point_marker_size": 14,
        "content_left": 80,
        "marker_left": 54,
        "point_gap": 18,
        "title_to_points_gap": 48,
        "bar_height": 28,
        "font_file": "TaipeiSansTCBeta-Bold.ttf",
        "regular_font_file": "TaipeiSansTCBeta-Regular.ttf",
        "point_indent_chars": 2,
        "point_text_indent": 20,
        "point_layout_width": 744,
    }
    style.update(styles[name])
    if name == "colorcard":
        if color_variant not in COLOR_CARD_VARIANTS:
            raise ValueError("不支援的色卡顏色")
        style.update(COLOR_CARD_VARIANTS[color_variant])
    style.setdefault("marker_shape", "square")
    style.setdefault("blocks", "none")
    style.setdefault("point_distribution", "flow")
    return style


def _font(size, bold=False, style=None):
    style = style or card_style()
    names = style["bold_fonts"] if bold else style["regular_fonts"]
    for name in names:
        directories = (
            os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
        )
        for directory in directories:
            path = os.path.join(directory, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap(draw, text, font, width):
    lines = []
    for paragraph in text.splitlines():
        current = ""
        for char in paragraph:
            if draw.textlength(current + char, font=font) > width and current:
                lines.append(current)
                current = char
            else:
                current += char
        if current:
            lines.append(current)
    return lines


def balanced_point_start(title_bottom, summary_start, point_heights, point_gap):
    """Return a point-group start that leaves equal space above and below."""
    total_height = sum(point_heights) + point_gap * max(0, len(point_heights) - 1)
    free_space = summary_start - title_bottom - total_height
    if free_space < 0:
        raise ValueError("Point content does not fit between title and summary")
    return title_bottom + free_space / 2


def vertical_section_start(section_top, section_bottom, content_height):
    """Return the Y coordinate that vertically centres text in a section."""
    if content_height > section_bottom - section_top:
        raise ValueError("Text content does not fit in its reserved section")
    return section_top + (section_bottom - section_top - content_height) / 2


def fit_font(draw, text, max_size, min_size, width, max_lines, bold=True, style=None):
    """Find the largest requested font weight that fits the allowed lines."""
    text = text.strip()
    if not text:
        return _font(max_size, bold, style), []
    for size in range(max_size, min_size - 1, -2):
        font = _font(size, bold, style)
        lines = _wrap(draw, text, font, width)
        if len(lines) <= max_lines:
            return font, lines
    raise ValueError("文字過長，無法在圖卡安全範圍內完整呈現")


def fit_point_font(draw, points, max_size, min_size, width, bold=True, style=None):
    """Find one font size that lets every point stay on a single line."""
    points = [point.strip() for point in points]
    for size in range(max_size, min_size - 1, -2):
        font = _font(size, bold, style)
        if all(len(_wrap(draw, point, font, width)) <= 1 for point in points):
            return font, points
    raise ValueError("重點文字過長，無法使用一致字級完整呈現")


def wrap_headline(draw, text, font, width):
    """Use up to two visually balanced lines for a centred Chinese headline."""
    text = text.strip()
    ordinary_lines = _wrap(draw, text, font, width)
    if len(ordinary_lines) <= 1:
        return ordinary_lines

    candidates = []
    for index in range(1, len(text)):
        left, right = text[:index].strip(), text[index:].strip()
        left_width = draw.textlength(left, font=font)
        right_width = draw.textlength(right, font=font)
        if left_width <= width and right_width <= width:
            candidates.append((abs(left_width - right_width), left, right))
    if candidates:
        _, left, right = min(candidates, key=lambda item: item[0])
        return [left, right]
    return ordinary_lines[:2]


def card_sections(generated):
    """Keep the promotional-card reading order independent from drawing details."""
    return [
        ("headline", generated.get("headline", "")),
        ("points", generated.get("key_points", [])[:3]),
        ("summary", generated.get("plain_summary", "")),
    ]


def _draw_texture(draw, style):
    if style["texture"] == "dots":
        for x in range(54, SIZE, 72):
            for y in range(56, SIZE - 40, 72):
                draw.ellipse((x, y, x + 4, y + 4), fill="#D6E8E2")
    elif style["texture"] == "lines":
        for y in range(64, SIZE - 40, 52):
            draw.line((48, y, SIZE - 48, y), fill="#ECE1D2", width=1)


def _draw_blocks(draw, style):
    if style["blocks"] == "three":
        draw.rectangle((48, 52, SIZE - 48, 480), fill="#D5DEE7")
        draw.rectangle((48, style["middle_block_top"], SIZE - 48, style["middle_block_bottom"]), fill=style["middle_block"])
        draw.rectangle((48, 846, SIZE - 48, 996), fill="#DDE5EC")


def _paste_cover(canvas, image_path, height):
    source = Image.open(image_path).convert("RGB")
    scale = max(SIZE / source.width, height / source.height)
    resized = source.resize((round(source.width * scale), round(source.height * scale)))
    left = (resized.width - SIZE) // 2
    top = (resized.height - height) // 2
    canvas.paste(resized.crop((left, top, left + SIZE, top + height)), (0, 0))


def _render_image_title_card(draft, output_dir, style):
    if not getattr(draft, "image_path", "") or not os.path.exists(draft.image_path):
        raise ValueError("請先上傳圖片再使用圖片標題版")
    image = Image.new("RGB", (SIZE, SIZE), style["background"])
    _paste_cover(image, draft.image_path, 625)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 570, SIZE, SIZE), fill=style["background"])
    draw.rectangle((0, 570, SIZE, 588), fill=style["accent"])
    generated = draft.generated or {}
    headline = generated.get("headline") or draft.topic
    headline_font, headline_lines = fit_font(draw, headline, 70, 42, 936, 2, style=style)
    y = 630
    line_height = round(headline_font.size * 1.16)
    for line in headline_lines:
        draw.text((72, y), line, font=headline_font, fill=style["text"])
        y += line_height
    points = (generated.get("key_points") or [])[:3]
    point_font, point_lines = fit_point_font(draw, points, 34, 22, 850, bold=False, style=style)
    y = max(805, y + 24)
    for point in point_lines:
        draw.rectangle((74, y + 12, 88, y + 26), fill=style["accent"])
        draw.text((110, y), point, font=point_font, fill="#E7EDF3")
        y += 58
    citation_text = "｜".join("{0}｜{1}".format(item["name"], item["date"]) for item in draft.citations[:3])
    citation_font, citation_lines = fit_font(draw, citation_text, 24, 14, 936, 1, bold=False, style=style)
    if citation_lines:
        draw.text((72, 1018), citation_lines[0], font=citation_font, fill=style["muted"])
    path = os.path.join(output_dir, "{0}.png".format(draft.draft_id))
    image.save(path, "PNG")
    return path


def render_card(draft, output_dir, style_name=None, color_variant=None):
    if not draft.approved:
        raise ValueError("草稿尚未核准")
    generated = draft.generated or {}
    os.makedirs(output_dir, exist_ok=True)
    style_name = style_name or getattr(draft, "style", "classic")
    color_variant = color_variant or getattr(draft, "color_variant", "black")
    style = card_style(style_name, color_variant)
    if style.get("image_title"):
        return _render_image_title_card(draft, output_dir, style)
    image = Image.new("RGB", (SIZE, SIZE), style["background"])
    draw = ImageDraw.Draw(image)
    navy, red, muted = style["text"], style["accent"], style["muted"]
    _draw_texture(draw, style)
    _draw_blocks(draw, style)
    if not style.get("full_bleed"):
        draw.rectangle((0, 0, SIZE, style["bar_height"]), fill=red)
    content_left, content_width = style["content_left"], 920
    top_start, top_end, title_area_end = 72, 850, 400
    summary_start, summary_end = 860, 990
    width = 920
    sections = dict(card_sections(generated))
    point_font, point_lines = fit_point_font(
        draw, sections["points"], style["point_size"], 50, style["point_layout_width"], bold=False, style=style
    )
    point_layouts = [(point_font, line) for line in point_lines]

    headline_text = sections["headline"] or draft.topic
    max_headline_font, _ = fit_font(
        draw, headline_text, style["headline_size"], 80, content_width, 2, style=style
    )
    point_heights = [round(font.size * 1.16) for font, _ in point_layouts]
    headline_font = None
    headline_lines = None
    for size in range(max_headline_font.size, 79, -2):
        candidate_font = _font(size, True, style)
        candidate_lines = _wrap(draw, headline_text.strip(), candidate_font, content_width)
        title_height = len(candidate_lines) * round(size * 1.16)
        point_spacing = style["title_to_points_gap"] + style["point_gap"] * max(0, len(point_layouts) - 1)
        total_height = title_height + sum(point_heights) + point_spacing
        title_limit = title_area_end - top_start
        if len(candidate_lines) <= 2 and title_height <= title_limit and total_height <= summary_start - top_start:
            headline_font, headline_lines = candidate_font, candidate_lines
            break
    if headline_font is None:
        raise ValueError("標題與重點過長，無法在圖卡上方完整呈現")

    title_line_height = round(headline_font.size * 1.16)
    used_height = len(headline_lines) * title_line_height + sum(point_heights)
    y = vertical_section_start(top_start, title_area_end, len(headline_lines) * title_line_height)
    for line in headline_lines:
        draw.text((content_left, y), line, font=headline_font, fill=navy)
        y += title_line_height
    point_positions = []
    if style["point_distribution"] == "even":
        area_top, area_bottom = y + style["title_to_points_gap"], summary_start
        if y + style["title_to_points_gap"] > area_top:
            raise ValueError("標題與重點色塊重疊")
        total_point_height = sum(point_heights)
        gap = style["point_gap"]
        if gap < 0:
            raise ValueError("重點無法平均置入色塊")
        point_y = balanced_point_start(y, summary_start, point_heights, gap)
        for point_height in point_heights:
            point_positions.append(round(point_y))
            point_y += point_height + gap
    for index, ((point_font, point_line), point_height) in enumerate(zip(point_layouts, point_heights)):
        if point_positions:
            y = point_positions[index]
        else:
            y += style["title_to_points_gap"] if index == 0 else style["point_gap"]
        marker_size = style["point_marker_size"]
        marker_y = y + max(0, (point_height - marker_size) // 2)
        point_text_left = content_left + style["point_text_indent"]
        marker_left = point_text_left - marker_size - 12
        if style["marker_shape"] == "circle":
            draw.ellipse((marker_left, marker_y, marker_left + marker_size, marker_y + marker_size), fill=red)
        else:
            draw.rectangle((marker_left, marker_y, marker_left + marker_size, marker_y + marker_size), fill=red)
        draw.text((point_text_left, y), point_line, font=point_font, fill=navy)
        y += point_height
    if not point_positions and y > top_end:
        raise ValueError("標題與重點超出圖卡上方版面")

    body_font, summary_lines = fit_font(
        draw, sections["summary"] or draft.content, 36, 22, content_width, 3, bold=False, style=style
    )
    body_line_height = round(body_font.size * 1.2)
    summary_height = len(summary_lines) * body_line_height
    if summary_start + summary_height > summary_end:
        raise ValueError("說明內容過長，無法在圖卡下方完整呈現")
    summary_y = vertical_section_start(summary_start, summary_end, summary_height)
    for line in summary_lines:
        draw.text((content_left, summary_y), line, font=body_font, fill=navy)
        summary_y += body_line_height
    citation_text = "　".join("{0}｜{1}".format(item["name"], item["date"]) for item in draft.citations[:3])
    citation_font, citation_lines = fit_font(draw, citation_text, 20, 12, 984, 1, bold=False, style=style)
    draw.line((48, 1006, 1032, 1006), fill=style["rule"], width=2)
    if citation_lines:
        draw.text((48, 1028), citation_lines[0], font=citation_font, fill=muted)
    if not style.get("full_bleed"):
        draw.rectangle((0, SIZE - style["bar_height"], SIZE, SIZE), fill=red)
    path = os.path.join(output_dir, "{0}.png".format(draft.draft_id))
    image.save(path, "PNG")
    return path
