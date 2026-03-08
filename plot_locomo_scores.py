#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        default="log",
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


def main() -> None:
    args = parse_args()
    test_dir = Path(args.log_dir) / args.test_id
    stats_path = test_dir / STATS_FILE_NAME
    output_path = test_dir / args.output_name

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

    # Canvas
    img_w, img_h = 1460, 980
    image = Image.new("RGB", (img_w, img_h), color=(248, 250, 252))
    draw = ImageDraw.Draw(image)

    font_title = load_font(30)
    font_subtitle = load_font(20)
    font_label = load_font(15)

    # Header
    draw.text((40, 24), "LoCoMo Score Summary", fill=(18, 18, 18), font=font_title)
    draw.text(
        (40, 66),
        f"test_id={args.test_id} | model={model_key} | overall_f1={overall_f1:.6f}"
        + (f" | overall_b1={overall_b1:.6f}" if has_b1 else ""),
        fill=(60, 60, 60),
        font=font_subtitle,
    )
    draw.text((40, 95), f"source: {stats_path}", fill=(95, 95, 95), font=font_label)

    # Accuracy panel
    draw_bar_chart(
        draw=draw,
        rows=rows_f1,
        x0=70,
        y0=150,
        width=1320,
        height=440,
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
        y0=700,
        width=1320,
        height=210,
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


if __name__ == "__main__":
    main()

