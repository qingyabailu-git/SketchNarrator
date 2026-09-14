import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from font_runtime import load_cjk_font


def preview_path(element: dict) -> tuple[tuple[int, int], tuple[int, int]]:
    """Use an explicit legacy handPath or derive a safe preview-only direction."""
    hand_path = element.get("handPath")
    if isinstance(hand_path, dict):
        start = hand_path.get("start")
        end = hand_path.get("end")
        if (
            isinstance(start, (list, tuple))
            and isinstance(end, (list, tuple))
            and len(start) == 2
            and len(end) == 2
        ):
            return (round(float(start[0])), round(float(start[1]))), (
                round(float(end[0])), round(float(end[1]))
            )

    region = element["region"]
    x, y = int(region["x"]), int(region["y"])
    width, height = int(region["width"]), int(region["height"])
    left, right = x + round(width * 0.18), x + round(width * 0.82)
    top, bottom = y + round(height * 0.18), y + round(height * 0.82)
    center_x, center_y = x + width // 2, y + height // 2
    direction = str((element.get("reveal") or {}).get("direction", "top_to_bottom"))
    paths = {
        "top_to_bottom": ((center_x, top), (center_x, bottom)),
        "bottom_to_top": ((center_x, bottom), (center_x, top)),
        "left_to_right": ((left, center_y), (right, center_y)),
        "right_to_left": ((right, center_y), (left, center_y)),
    }
    return paths.get(direction, paths["top_to_bottom"])


def main(image_path: str, annotation_path: str, output_path: str) -> None:
    image = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = load_cjk_font(28)
    small_font = load_cjk_font(18)
    colors = [(38, 103, 255, 225), (255, 105, 92, 225), (41, 167, 102, 225), (181, 100, 255, 225)]

    data = json.loads(Path(annotation_path).read_text(encoding="utf-8"))
    for index, element in enumerate(data["elements"], start=1):
        region = element["region"]
        x, y = region["x"], region["y"]
        right, bottom = x + region["width"], y + region["height"]
        color = colors[(index - 1) % len(colors)]
        fill = (*color[:3], 24)
        draw.rounded_rectangle((x, y, right, bottom), radius=12, outline=color, width=4, fill=fill)
        draw.ellipse((x + 8, y + 8, x + 44, y + 44), fill=color)
        draw.text((x + 19, y + 8), str(index), anchor="ma", font=small_font, fill="white")
        label = f"{index}. {element['label']}  {element['reveal']['direction']}"
        draw.rounded_rectangle((x + 52, y + 8, min(right - 8, x + 52 + len(label) * 19), y + 46), radius=6, fill=(255, 255, 255, 225))
        draw.text((x + 60, y + 12), label, font=small_font, fill=color)
        start, end = preview_path(element)
        draw.line((start, end), fill=color, width=4)
        draw.ellipse((end[0] - 7, end[1] - 7, end[0] + 7, end[1] + 7), fill=color)

    result = Image.alpha_composite(image, overlay).convert("RGB")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path, quality=95)


if __name__ == "__main__":
    main(*sys.argv[1:4])
