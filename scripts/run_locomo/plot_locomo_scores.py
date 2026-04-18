#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import colorsys
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from _bootstrap import bootstrap_project


bootstrap_project()

from m_agent.paths import LOG_DIR, resolve_project_path
from PIL import Image, ImageDraw, ImageFont


CATEGORY_ORDER = [4, 1, 2, 3, 5]
CATEGORY_NAMES = {
    4: "single-hop",
    1: "multi-hop",
    2: "temporal",
    3: "commonsense",
    5: "adversarial",
}
STATS_FILE_NAME = "locomo10_agent_qa_stats.json"
OUTPUT_FILE_NAME = "locomo_scores.png"
RADAR_OUTPUT_FILE_NAME = "locomo_ability_radar.png"
RADAR_AXES = [
    ("single_hop", "single-hop"),
    ("multi_hop", "multi-hop"),
    ("temporal", "temporal"),
    ("open_domain", "open-domain"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Draw LoCoMo score charts for one test_id and save image in the same log directory."
    )
    parser.add_argument(
        "--test-id",
        required=True,
        help="Sub-directory name under log/, e.g. test1",
    )
    parser.add_argument(
        "--log-dir",
        default=str(LOG_DIR),
        help="Base log directory (default: log)",
    )
    parser.add_argument(
        "--model-key",
        default="",
        help="Optional model key in stats json. If empty, auto-pick the first one.",
    )
    parser.add_argument(
        "--output-name",
        default=OUTPUT_FILE_NAME,
        help=f"Output image file name inside the test directory (default: {OUTPUT_FILE_NAME})",
    )
    parser.add_argument(
        "--other-methods-path",
        default="other_methods.json",
        help="Path to other methods json. Relative path is resolved against --log-dir.",
    )
    parser.add_argument(
        "--radar-output-name",
        default=RADAR_OUTPUT_FILE_NAME,
        help=f"Output image file name for ability radar chart inside test directory (default: {RADAR_OUTPUT_FILE_NAME})",
    )
    return parser.parse_args()


def load_font(size: int) -> ImageFont.ImageFont:
    font_candidates = [
        "arial.ttf",
        "DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for font_path in font_candidates:
        try:
            return ImageFont.truetype(font_path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def normalize_percent(value: float) -> float:
    if value <= 1.0:
        return value * 100.0
    return value


def resolve_path(base_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return base_dir / candidate


def pick_model_stats(stats_obj: Dict[str, Any], model_key: str) -> tuple[str, Dict[str, Any]]:
    if model_key:
        if model_key not in stats_obj:
            raise KeyError(f"model_key '{model_key}' not found in stats file.")
        model_stats = stats_obj.get(model_key)
        if not isinstance(model_stats, dict):
            raise ValueError(f"stats['{model_key}'] is not a dict.")
        return model_key, model_stats

    # If the file is already in plain stats shape, use it directly.
    if "summary_by_category" in stats_obj:
        return "default", stats_obj

    for key, value in stats_obj.items():
        if isinstance(value, dict) and "summary_by_category" in value:
            return str(key), value

    raise ValueError("No valid model stats found: missing 'summary_by_category'.")


def extract_by_category(summary: Dict[str, Any]) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for cat in CATEGORY_ORDER:
        cat_key = str(cat)
        item = summary.get(cat_key, {})
        if not isinstance(item, dict):
            item = {}
        rows.append(
            {
                "category": cat,
                "count": to_float(item.get("count", 0.0)),
                "score_sum": to_float(item.get("score_sum", 0.0)),
                "accuracy": to_float(item.get("accuracy", 0.0)),
            }
        )
    return rows


def rows_to_map(rows: Sequence[Dict[str, float]]) -> Dict[int, Dict[str, float]]:
    mapping: Dict[int, Dict[str, float]] = {}
    for row in rows:
        mapping[int(row["category"])] = row
    return mapping


def load_other_methods(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return []
    results = raw.get("results", [])
    if not isinstance(results, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in results:
        if isinstance(item, dict):
            normalized.append(item)
    return normalized


def build_current_ability_values(rows_f1: Sequence[Dict[str, float]]) -> Tuple[List[float], str]:
    cat_map = rows_to_map(rows_f1)

    def get_accuracy(cat: int) -> float:
        row = cat_map.get(cat, {})
        return to_float(row.get("accuracy", 0.0))

    def get_count(cat: int) -> float:
        row = cat_map.get(cat, {})
        return to_float(row.get("count", 0.0))

    open_domain_cat = 3
    note = ""
    if get_count(3) <= 0 and get_count(5) > 0:
        open_domain_cat = 5
        note = "Radar mapping note: open-domain uses C5(adversarial), because C3 count is 0."

    values = [
        normalize_percent(get_accuracy(4)),  # single-hop
        normalize_percent(get_accuracy(1)),  # multi-hop
        normalize_percent(get_accuracy(2)),  # temporal
        normalize_percent(get_accuracy(open_domain_cat)),  # open-domain
    ]
    return values, note


def text_wh(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    font: ImageFont.ImageFont,
    fill: Tuple[int, int, int],
) -> None:
    w, h = text_wh(draw, text, font)
    draw.text((x - w / 2, y - h / 2), text, fill=fill, font=font)


def generate_distinct_colors(n: int) -> List[Tuple[int, int, int]]:
    colors: List[Tuple[int, int, int]] = []
    if n <= 0:
        return colors
    for i in range(n):
        # Golden-ratio hue stepping avoids duplicates and keeps contrast stable.
        hue = (0.07 + i * 0.61803398875) % 1.0
        sat = 0.72
        val = 0.90
        r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
        colors.append((int(r * 255), int(g * 255), int(b * 255)))
    return colors


def draw_axes(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    width: int,
    height: int,
    y_max: float,
    y_ticks: int,
    font: ImageFont.ImageFont,
    y_label_fmt: str = "{:.1f}",
) -> None:
    # Axes
    draw.line([(x0, y0), (x0, y0 + height)], fill=(40, 40, 40), width=2)
    draw.line([(x0, y0 + height), (x0 + width, y0 + height)], fill=(40, 40, 40), width=2)

    # Grid lines + y labels
    for i in range(y_ticks + 1):
        ratio = i / y_ticks
        y = int(y0 + height - ratio * height)
        val = ratio * y_max
        grid_color = (225, 225, 225) if i != 0 else (40, 40, 40)
        draw.line([(x0, y), (x0 + width, y)], fill=grid_color, width=1)
        label = y_label_fmt.format(val)
        draw.text((x0 - 50, y - 8), label, fill=(80, 80, 80), font=font)


def draw_bar_chart(
    draw: ImageDraw.ImageDraw,
    rows: List[Dict[str, float]],
    x0: int,
    y0: int,
    width: int,
    height: int,
    title: str,
    font_title: ImageFont.ImageFont,
    font_label: ImageFont.ImageFont,
    values_a: List[float],
    values_b: Optional[List[float]],
    color_a: tuple[int, int, int],
    color_b: tuple[int, int, int],
    legend_a: str,
    legend_b: Optional[str],
    y_max: float,
    y_fmt: str,
) -> None:
    draw.text((x0, y0 - 34), title, fill=(20, 20, 20), font=font_title)
    draw_axes(draw, x0, y0, width, height, y_max=y_max, y_ticks=5, font=font_label, y_label_fmt=y_fmt)

    n = len(rows)
    group_w = width / n
    bar_w = group_w * (0.22 if values_b is not None else 0.42)

    for idx, row in enumerate(rows):
        cx = x0 + group_w * idx + group_w * 0.5

        # A bar
        a = values_a[idx]
        a_h = 0 if y_max <= 0 else (a / y_max) * height
        ax1 = int(cx - bar_w - 4) if values_b is not None else int(cx - bar_w / 2)
        ax2 = int(cx - 4) if values_b is not None else int(cx + bar_w / 2)
        ay1 = int(y0 + height - a_h)
        ay2 = y0 + height
        draw.rectangle([(ax1, ay1), (ax2, ay2)], fill=color_a, outline=(20, 20, 20))
        draw.text((ax1, max(y0 + 2, ay1 - 16)), f"{a:.3f}" if y_max <= 1.0 else f"{a:.0f}", fill=(30, 30, 30), font=font_label)

        if values_b is not None:
            b = values_b[idx]
            b_h = 0 if y_max <= 0 else (b / y_max) * height
            bx1 = int(cx + 4)
            bx2 = int(cx + 4 + bar_w)
            by1 = int(y0 + height - b_h)
            by2 = y0 + height
            draw.rectangle([(bx1, by1), (bx2, by2)], fill=color_b, outline=(20, 20, 20))
            draw.text((bx1, max(y0 + 2, by1 - 16)), f"{b:.3f}" if y_max <= 1.0 else f"{b:.0f}", fill=(30, 30, 30), font=font_label)

        cat = int(row["category"])
        xlab = f"C{cat}\n{CATEGORY_NAMES.get(cat, 'unknown')}"
        draw.multiline_text((int(cx - group_w * 0.34), y0 + height + 8), xlab, fill=(50, 50, 50), font=font_label, spacing=1)

    # Legend
    lx = x0 + width - 280
    ly = y0 - 30
    draw.rectangle([(lx, ly), (lx + 14, ly + 14)], fill=color_a, outline=(20, 20, 20))
    draw.text((lx + 20, ly - 1), legend_a, fill=(40, 40, 40), font=font_label)
    if legend_b:
        draw.rectangle([(lx + 110, ly), (lx + 124, ly + 14)], fill=color_b, outline=(20, 20, 20))
        draw.text((lx + 130, ly - 1), legend_b, fill=(40, 40, 40), font=font_label)


def draw_radar_chart(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    width: int,
    height: int,
    title: str,
    axis_labels: Sequence[str],
    series: Sequence[Tuple[str, Sequence[float], Tuple[int, int, int]]],
    font_title: ImageFont.ImageFont,
    font_label: ImageFont.ImageFont,
    max_value: float = 100.0,
) -> None:
    draw.text((x0, y0 - 34), title, fill=(20, 20, 20), font=font_title)

    n = len(axis_labels)
    if n < 3:
        return

    cx = x0 + int(width * 0.30)
    cy = y0 + int(height * 0.55)
    radius = int(min(width * 0.42, height * 0.86) / 2)
    start_angle = -math.pi / 2
    angles = [start_angle + 2 * math.pi * i / n for i in range(n)]

    # Grid polygons
    for level in range(1, 6):
        ratio = level / 5.0
        r = radius * ratio
        ring = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in angles]
        draw.polygon(ring, outline=(220, 220, 220))
        tick_text = f"{max_value * ratio:.0f}"
        draw.text((cx + 8, cy - r - 8), tick_text, fill=(120, 120, 120), font=font_label)

    # Axis lines + labels
    for angle, label in zip(angles, axis_labels):
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        draw.line([(cx, cy), (x, y)], fill=(210, 210, 210), width=1)

        lx = cx + (radius + 28) * math.cos(angle)
        ly = cy + (radius + 28) * math.sin(angle)
        draw_centered_text(draw, lx, ly, label, font_label, (70, 70, 70))

    # Series polygons
    for method, values, color in series:
        points: List[Tuple[float, float]] = []
        for angle, raw in zip(angles, values):
            v = max(0.0, min(max_value, to_float(raw)))
            r = radius * (v / max_value if max_value > 0 else 0.0)
            px = cx + r * math.cos(angle)
            py = cy + r * math.sin(angle)
            points.append((px, py))

        if len(points) >= 3:
            draw.line(points + [points[0]], fill=color, width=3)
            for px, py in points:
                draw.ellipse([(px - 3, py - 3), (px + 3, py + 3)], fill=color, outline=(255, 255, 255))

    # Legend
    legend_x = x0 + int(width * 0.58)
    legend_y = y0 + 10
    for idx, (method, _, color) in enumerate(series):
        y = legend_y + idx * 26
        draw.rectangle([(legend_x, y), (legend_x + 14, y + 14)], fill=color, outline=(30, 30, 30))
        draw.text((legend_x + 20, y - 2), method, fill=(50, 50, 50), font=font_label)


def main() -> None:
    args = parse_args()
    test_dir = resolve_project_path(args.log_dir) / args.test_id
    stats_path = test_dir / STATS_FILE_NAME
    output_path = test_dir / args.output_name
    radar_output_path = test_dir / args.radar_output_name
    other_methods_path = resolve_path(resolve_project_path(args.log_dir), args.other_methods_path)

    if not stats_path.exists():
        raise FileNotFoundError(f"Stats file not found: {stats_path}")

    with open(stats_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid stats json root type: {type(raw)}")

    model_key, model_stats = pick_model_stats(raw, args.model_key)

    summary_f1 = model_stats.get("summary_by_category", {})
    if not isinstance(summary_f1, dict):
        raise ValueError("Missing or invalid 'summary_by_category'")
    summary_b1 = model_stats.get("summary_by_category_b1", {})
    has_b1 = isinstance(summary_b1, dict) and len(summary_b1) > 0

    rows_f1 = extract_by_category(summary_f1)
    rows_b1 = extract_by_category(summary_b1) if has_b1 else []

    f1_values = [r["accuracy"] for r in rows_f1]
    b1_values = [r["accuracy"] for r in rows_b1] if has_b1 else None
    count_values = [r["count"] for r in rows_f1]

    overall_f1 = to_float(model_stats.get("overall_accuracy", 0.0))
    overall_b1 = to_float(model_stats.get("overall_b1", 0.0))

    # Radar comparison data (current run + other methods)
    current_ability_values, mapping_note = build_current_ability_values(rows_f1)
    radar_entries: List[Tuple[str, Sequence[float]]] = [
        (
            f"{args.test_id}:{model_key} (F1 {normalize_percent(overall_f1):.2f})",
            current_ability_values,
        )
    ]

    other_methods = load_other_methods(other_methods_path)
    for idx, item in enumerate(other_methods):
        method_name = str(item.get("method", f"method-{idx+1}"))
        values: List[float] = []
        for axis_key, _ in RADAR_AXES:
            axis_obj = item.get(axis_key, {})
            if not isinstance(axis_obj, dict):
                axis_obj = {}
            values.append(normalize_percent(to_float(axis_obj.get("F1", 0.0))))

        overall_obj = item.get("overall", {})
        if isinstance(overall_obj, dict):
            method_overall = normalize_percent(to_float(overall_obj.get("F1", 0.0)))
        else:
            method_overall = 0.0

        radar_entries.append(
            (
                f"{method_name} (F1 {method_overall:.2f})",
                values,
            )
        )
    radar_colors = generate_distinct_colors(len(radar_entries))
    radar_series: List[Tuple[str, Sequence[float], Tuple[int, int, int]]] = [
        (name, values, radar_colors[idx]) for idx, (name, values) in enumerate(radar_entries)
    ]

    # Dynamic layout for summary chart (without radar panel)
    header_lines = [
        f"test_id={args.test_id} | model={model_key} | overall_f1={overall_f1:.6f}"
        + (f" | overall_b1={overall_b1:.6f}" if has_b1 else ""),
        f"source: {stats_path}",
    ]

    accuracy_y = 66 + len(header_lines) * 28 + 20
    accuracy_h = 430
    count_y = accuracy_y + accuracy_h + 90
    count_h = 210

    # Canvas
    img_w = 1620
    img_h = count_y + count_h + 130
    image = Image.new("RGB", (img_w, img_h), color=(248, 250, 252))
    draw = ImageDraw.Draw(image)

    font_title = load_font(30)
    font_subtitle = load_font(20)
    font_label = load_font(15)

    # Header
    draw.text((40, 24), "LoCoMo Score Summary", fill=(18, 18, 18), font=font_title)
    for idx, line in enumerate(header_lines):
        y = 66 + idx * 28
        draw.text((40, y), line, fill=(60, 60, 60), font=font_subtitle if idx == 0 else font_label)

    # Accuracy panel
    draw_bar_chart(
        draw=draw,
        rows=rows_f1,
        x0=70,
        y0=accuracy_y,
        width=1480,
        height=accuracy_h,
        title="Category Accuracy",
        font_title=font_subtitle,
        font_label=font_label,
        values_a=f1_values,
        values_b=b1_values,
        color_a=(79, 70, 229),   # indigo
        color_b=(14, 165, 233),  # sky
        legend_a="F1",
        legend_b="B1" if has_b1 else None,
        y_max=1.0,
        y_fmt="{:.1f}",
    )

    # Count panel
    draw_bar_chart(
        draw=draw,
        rows=rows_f1,
        x0=70,
        y0=count_y,
        width=1480,
        height=count_h,
        title="Question Count by Category",
        font_title=font_subtitle,
        font_label=font_label,
        values_a=count_values,
        values_b=None,
        color_a=(16, 185, 129),  # emerald
        color_b=(0, 0, 0),
        legend_a="count",
        legend_b=None,
        y_max=max(count_values) * 1.15 if count_values else 1.0,
        y_fmt="{:.0f}",
    )

    test_dir.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    print(f"Saved: {output_path}")

    # Build standalone radar image
    radar_header_lines = [
        f"test_id={args.test_id} | model={model_key} | F1 radar comparison",
        f"source: {stats_path}",
        f"comparison source: {other_methods_path}",
    ]
    if mapping_note:
        radar_header_lines.append(mapping_note)

    radar_y = 66 + len(radar_header_lines) * 28 + 20
    radar_h = max(360, 120 + 26 * len(radar_series))
    radar_img_w = 1620
    radar_img_h = radar_y + radar_h + 100

    radar_image = Image.new("RGB", (radar_img_w, radar_img_h), color=(248, 250, 252))
    radar_draw = ImageDraw.Draw(radar_image)
    radar_draw.text((40, 24), "LoCoMo Ability Radar", fill=(18, 18, 18), font=font_title)
    for idx, line in enumerate(radar_header_lines):
        y = 66 + idx * 28
        radar_draw.text((40, y), line, fill=(60, 60, 60), font=font_subtitle if idx == 0 else font_label)

    draw_radar_chart(
        draw=radar_draw,
        x0=70,
        y0=radar_y,
        width=1480,
        height=radar_h,
        title="Ability Radar (F1, unit: %)",
        axis_labels=[label for _, label in RADAR_AXES],
        series=radar_series,
        font_title=font_subtitle,
        font_label=font_label,
        max_value=100.0,
    )
    radar_image.save(radar_output_path)
    print(f"Saved: {radar_output_path}")


if __name__ == "__main__":
    main()
