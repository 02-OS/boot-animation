#!/usr/bin/env python3
"""Regenerate compact Plymouth assets from the approved 02 vector master.

Requires Python, pycairo, and Pillow only when editing the art. The ISO build
uses the committed PNGs and needs no render-time Python dependencies.
"""
from __future__ import annotations

import io
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import cairo
from PIL import Image

ROOT = Path(__file__).resolve().parent
SPEC_PATH = ROOT / "portal-spec.json"
OUTPUT = ROOT / "airootfs/usr/share/plymouth/themes/02-zero-portal"
WIDTH = 1280
CANVAS = (512, 448)
URL_REF_RE = re.compile(r"url\(\s*#([^)\s]+)\s*\)")
NUMBER_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
TRANSLATE_RE = re.compile(r"translate\(\s*([-+]?(?:\d*\.\d+|\d+))\s*[,\s]\s*([-+]?(?:\d*\.\d+|\d+))\s*\)")


def load_spec(spec_path: Path = SPEC_PATH) -> dict:
    """Load canonical portal geometry and timing constants."""
    return json.loads(spec_path.read_text(encoding="utf-8"))


def _parse_backdrop(color: str) -> tuple[int, int, int]:
    cleaned = color.strip().lstrip("#")
    if len(cleaned) != 6:
        raise ValueError(f"Unsupported backdrop hex color: {color!r}")
    return tuple(int(cleaned[index:index + 2], 16) for index in (0, 2, 4))


def _local_tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1]


def _parse_style_attr(element: ET.Element) -> dict[str, str]:
    style = {}
    raw = element.get("style", "")
    for part in raw.split(";"):
        if ":" in part:
            key, value = part.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key:
                style[key] = value
    return style


def _merged_presentation(element: ET.Element, inherited: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(inherited or {})
    inline = _parse_style_attr(element)
    for key in ("stroke", "fill", "stroke-width"):
        value = inline.get(key)
        if value is None:
            value = element.get(key)
        if value is not None:
            merged[key] = value.strip()
    return merged


def _collect_gradients(master: ET.Element) -> tuple[dict[str, tuple[list[float], list[list[float]]]], tuple[list[float], list[list[float]]]]:
    raw_grads: dict[str, ET.Element] = {}
    ordered: list[ET.Element] = []
    for element in master.iter():
        if _local_tag(element) == "linearGradient":
            ordered.append(element)
            grad_id = element.get("id")
            if grad_id:
                raw_grads[grad_id] = element
    if not ordered:
        raise ValueError("No linearGradient found in SVG master")

    def resolve_grad(element: ET.Element, seen: set[str] | None = None) -> tuple[list[float], list[list[float]]]:
        seen = set(seen or ())
        href = element.get("href") or element.get("{http://www.w3.org/1999/xlink}href")
        base_points = [0.0, 0.0, 1.0, 0.0]
        base_stops: list[list[float]] = []
        if href and href.startswith("#"):
            ref_id = href[1:]
            if ref_id in raw_grads and ref_id not in seen:
                seen.add(ref_id)
                base_points, base_stops = resolve_grad(raw_grads[ref_id], seen)
        points = [
            float(element.get(key)) if element.get(key) is not None else base_points[idx]
            for idx, key in enumerate(("x1", "y1", "x2", "y2"))
        ]
        stops: list[list[float]] = []
        for stop in element:
            if _local_tag(stop) != "stop":
                continue
            stop_style = _parse_style_attr(stop)
            raw_color = stop_style.get("stop-color") or stop.get("stop-color")
            if not raw_color:
                continue
            color = raw_color.lstrip("#")
            offset = float(stop.get("offset", "0"))
            stops.append([offset, *(int(color[index:index + 2], 16) / 255 for index in (0, 2, 4))])
        return points, (stops or base_stops)

    resolved_by_id = {grad_id: resolve_grad(elem, {grad_id}) for grad_id, elem in raw_grads.items()}
    fallback = resolve_grad(ordered[0])
    return resolved_by_id, fallback


def _default_stroke_width(master: ET.Element) -> float:
    for element in master.iter():
        attrs = _merged_presentation(element)
        if "stroke-width" in attrs:
            return float(attrs["stroke-width"])
    return 1.0


def _find_path_with_attrs(element: ET.Element, path_id: str, inherited: dict[str, str] | None = None):
    attrs = _merged_presentation(element, inherited)
    if _local_tag(element) == "path" and element.get("id") == path_id:
        return element, attrs
    for child in element:
        found = _find_path_with_attrs(child, path_id, attrs)
        if found is not None:
            return found
    return None


def logo_style(master: ET.Element, path_id: str | None = None, attrs: dict[str, str] | None = None):
    """Resolve linear gradient and stroke-width for a path or the master logo."""
    gradients, fallback_grad = _collect_gradients(master)
    fallback_stroke = _default_stroke_width(master)

    resolved_attrs = dict(attrs or {})
    if path_id is not None and not resolved_attrs:
        found = _find_path_with_attrs(master, path_id)
        if found is not None:
            _, resolved_attrs = found

    grad = fallback_grad
    for key in ("stroke", "fill"):
        prop = resolved_attrs.get(key, "")
        if not prop or prop == "none":
            continue
        match = URL_REF_RE.search(prop)
        if match and match.group(1) in gradients:
            grad = gradients[match.group(1)]
            break

    stroke = fallback_stroke
    if "stroke-width" in resolved_attrs:
        stroke = float(resolved_attrs["stroke-width"])

    points, stops = grad
    return points, stops, stroke


class _PathReader:
    def __init__(self, path: str):
        self.path = path
        self.pos = 0
        self.length = len(path)

    def _skip_sep(self):
        while self.pos < self.length and self.path[self.pos] in " \t\r\n,":
            self.pos += 1

    def has_more(self) -> bool:
        self._skip_sep()
        return self.pos < self.length

    def peek_is_command(self) -> bool:
        self._skip_sep()
        return self.pos < self.length and self.path[self.pos].isalpha()

    def next_command(self) -> str:
        self._skip_sep()
        cmd = self.path[self.pos]
        self.pos += 1
        return cmd

    def next_number(self) -> float:
        self._skip_sep()
        match = NUMBER_RE.match(self.path, self.pos)
        if not match:
            raise ValueError(f"Expected number at index {self.pos} in SVG path: {self.path!r}")
        self.pos = match.end()
        return float(match.group(0))

    def next_flag(self) -> int:
        self._skip_sep()
        if self.pos >= self.length or self.path[self.pos] not in ("0", "1"):
            raise ValueError(f"Expected arc flag (0 or 1) at index {self.pos} in SVG path: {self.path!r}")
        flag = int(self.path[self.pos])
        self.pos += 1
        return flag


def _arc_to_cubics(x1: float, y1: float, rx: float, ry: float, phi_deg: float,
                   large_arc: int, sweep: int, x2: float, y2: float) -> list[list]:
    if (x1, y1) == (x2, y2):
        return []
    if rx == 0.0 or ry == 0.0:
        return [["L", x2, y2]]

    rx = abs(rx)
    ry = abs(ry)
    phi = math.radians(phi_deg % 360.0)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    dx2 = (x1 - x2) / 2.0
    dy2 = (y1 - y2) / 2.0
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2

    rx2 = rx * rx
    ry2 = ry * ry
    x1p2 = x1p * x1p
    y1p2 = y1p * y1p

    lam = x1p2 / rx2 + y1p2 / ry2
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale
        rx2 = rx * rx
        ry2 = ry * ry

    num = max(0.0, rx2 * ry2 - rx2 * y1p2 - ry2 * x1p2)
    den = rx2 * y1p2 + ry2 * x1p2
    factor = math.sqrt(num / den) if den > 0.0 else 0.0
    if large_arc == sweep:
        factor = -factor

    cxp = factor * (rx * y1p) / ry
    cyp = factor * -(ry * x1p) / rx
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def vec_angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        mag = math.hypot(ux, uy) * math.hypot(vx, vy)
        ratio = max(-1.0, min(1.0, dot / mag)) if mag > 0.0 else 1.0
        angle = math.acos(ratio)
        return -angle if (ux * vy - uy * vx) < 0.0 else angle

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta1 = vec_angle(1.0, 0.0, ux, uy)
    dtheta = vec_angle(ux, uy, vx, vy)
    if not sweep and dtheta > 0.0:
        dtheta -= 2.0 * math.pi
    elif sweep and dtheta < 0.0:
        dtheta += 2.0 * math.pi

    segments = max(1, math.ceil(abs(dtheta) / (math.pi / 2.0)))
    delta = dtheta / segments
    alpha = (4.0 / 3.0) * math.tan(delta / 4.0)

    def map_point(px: float, py: float) -> tuple[float, float]:
        return (
            cos_phi * (rx * px) - sin_phi * (ry * py) + cx,
            sin_phi * (rx * px) + cos_phi * (ry * py) + cy,
        )

    cubics = []
    for idx in range(segments):
        t1 = theta1 + idx * delta
        t2 = t1 + delta
        cos1, sin1 = math.cos(t1), math.sin(t1)
        cos2, sin2 = math.cos(t2), math.sin(t2)
        c1x, c1y = map_point(cos1 - alpha * sin1, sin1 + alpha * cos1)
        c2x, c2y = map_point(cos2 + alpha * sin2, sin2 - alpha * cos2)
        ex, ey = map_point(cos2, sin2) if idx < segments - 1 else (x2, y2)
        cubics.append(["C", c1x, c1y, c2x, c2y, ex, ey])
    return cubics


def path_commands(path: str) -> list[list]:
    """Parse an SVG path string into normalized absolute M/L/C/Z commands."""
    reader = _PathReader(path)
    current = (0.0, 0.0)
    start = current
    commands: list[list] = []
    prev_op = ""
    last_cubic_cp: tuple[float, float] | None = None
    last_quad_cp: tuple[float, float] | None = None
    active_op = ""

    while reader.has_more():
        if reader.peek_is_command():
            operation = reader.next_command()
            active_op = operation
        elif active_op:
            operation = active_op
        else:
            raise ValueError(f"SVG path missing initial command: {path!r}")

        if operation in ("Z", "z"):
            commands.append(["Z"])
            current = start
            prev_op = operation
            last_cubic_cp = None
            last_quad_cp = None
            active_op = ""
            continue

        rel = operation.islower()
        op_upper = operation.upper()

        if op_upper == "M":
            x = reader.next_number() + (current[0] if rel else 0.0)
            y = reader.next_number() + (current[1] if rel else 0.0)
            current = start = (x, y)
            commands.append(["M", x, y])
            last_cubic_cp = None
            last_quad_cp = None
            prev_op = operation
            active_op = "l" if rel else "L"
        elif op_upper == "L":
            x = reader.next_number() + (current[0] if rel else 0.0)
            y = reader.next_number() + (current[1] if rel else 0.0)
            current = (x, y)
            commands.append(["L", x, y])
            last_cubic_cp = None
            last_quad_cp = None
            prev_op = operation
        elif op_upper == "H":
            x = reader.next_number() + (current[0] if rel else 0.0)
            current = (x, current[1])
            commands.append(["L", *current])
            last_cubic_cp = None
            last_quad_cp = None
            prev_op = operation
        elif op_upper == "V":
            y = reader.next_number() + (current[1] if rel else 0.0)
            current = (current[0], y)
            commands.append(["L", *current])
            last_cubic_cp = None
            last_quad_cp = None
            prev_op = operation
        elif op_upper == "C":
            ox, oy = current if rel else (0.0, 0.0)
            x1, y1 = reader.next_number() + ox, reader.next_number() + oy
            x2, y2 = reader.next_number() + ox, reader.next_number() + oy
            x, y = reader.next_number() + ox, reader.next_number() + oy
            commands.append(["C", x1, y1, x2, y2, x, y])
            last_cubic_cp = (x2, y2)
            last_quad_cp = None
            current = (x, y)
            prev_op = operation
        elif op_upper == "S":
            ox, oy = current if rel else (0.0, 0.0)
            if prev_op in ("C", "c", "S", "s") and last_cubic_cp is not None:
                x1 = 2.0 * current[0] - last_cubic_cp[0]
                y1 = 2.0 * current[1] - last_cubic_cp[1]
            else:
                x1, y1 = current
            x2, y2 = reader.next_number() + ox, reader.next_number() + oy
            x, y = reader.next_number() + ox, reader.next_number() + oy
            commands.append(["C", x1, y1, x2, y2, x, y])
            last_cubic_cp = (x2, y2)
            last_quad_cp = None
            current = (x, y)
            prev_op = operation
        elif op_upper == "Q":
            ox, oy = current if rel else (0.0, 0.0)
            x0, y0 = current
            x1, y1 = reader.next_number() + ox, reader.next_number() + oy
            x2, y2 = reader.next_number() + ox, reader.next_number() + oy
            commands.append([
                "C",
                x0 + 2.0 * (x1 - x0) / 3.0,
                y0 + 2.0 * (y1 - y0) / 3.0,
                x2 + 2.0 * (x1 - x2) / 3.0,
                y2 + 2.0 * (y1 - y2) / 3.0,
                x2,
                y2,
            ])
            last_quad_cp = (x1, y1)
            last_cubic_cp = None
            current = (x2, y2)
            prev_op = operation
        elif op_upper == "T":
            ox, oy = current if rel else (0.0, 0.0)
            x0, y0 = current
            if prev_op in ("Q", "q", "T", "t") and last_quad_cp is not None:
                x1 = 2.0 * x0 - last_quad_cp[0]
                y1 = 2.0 * y0 - last_quad_cp[1]
            else:
                x1, y1 = current
            x2, y2 = reader.next_number() + ox, reader.next_number() + oy
            commands.append([
                "C",
                x0 + 2.0 * (x1 - x0) / 3.0,
                y0 + 2.0 * (y1 - y0) / 3.0,
                x2 + 2.0 * (x1 - x2) / 3.0,
                y2 + 2.0 * (y1 - y2) / 3.0,
                x2,
                y2,
            ])
            last_quad_cp = (x1, y1)
            last_cubic_cp = None
            current = (x2, y2)
            prev_op = operation
        elif op_upper == "A":
            ox, oy = current if rel else (0.0, 0.0)
            rx = reader.next_number()
            ry = reader.next_number()
            rot = reader.next_number()
            large_arc = reader.next_flag()
            sweep = reader.next_flag()
            x = reader.next_number() + ox
            y = reader.next_number() + oy
            commands.extend(_arc_to_cubics(current[0], current[1], rx, ry, rot, large_arc, sweep, x, y))
            last_cubic_cp = None
            last_quad_cp = None
            current = (x, y)
            prev_op = operation
        else:
            raise ValueError(f"Unsupported SVG path command: {operation!r}")

    return commands


def path_to_cairo(ctx, path):
    for operation, *arguments in path_commands(path):
        if operation == "M":
            ctx.move_to(*arguments)
        elif operation == "L":
            ctx.line_to(*arguments)
        elif operation == "C":
            ctx.curve_to(*arguments)
        else:
            ctx.close_path()


def draw_tree(ctx, element, master=None, inherited=None):
    master = master if master is not None else element
    attrs = _merged_presentation(element, inherited)
    ctx.save()
    transform = element.get("transform", "")
    if transform:
        match = TRANSLATE_RE.fullmatch(transform.strip())
        if not match:
            raise ValueError(f"Unsupported vector transform: {transform}")
        ctx.translate(*map(float, match.groups()))
    if _local_tag(element) == "path":
        points, stops, stroke = logo_style(master, path_id=element.get("id"), attrs=attrs)
        ink = cairo.LinearGradient(*points)
        for stop in stops:
            ink.add_color_stop_rgb(*stop)
        ctx.set_source(ink)
        ctx.set_line_width(stroke)
        ctx.new_path()
        path_to_cairo(ctx, element.get("d"))
        if attrs.get("stroke") == "none":
            ctx.fill()
        else:
            ctx.stroke()
    for child in element:
        draw_tree(ctx, child, master=master, inherited=attrs)
    ctx.restore()


def _viewbox_width(master: ET.Element) -> float:
    viewbox = master.get("viewBox", "")
    parts = viewbox.split()
    if len(parts) == 4:
        return float(parts[2])
    return float(master.get("width", "680"))


def logo_image(master, width=WIDTH, canvas=CANVAS, supersample=3):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                 canvas[0] * supersample, canvas[1] * supersample)
    ctx = cairo.Context(surface)
    ctx.scale(supersample, supersample)
    ctx.translate(canvas[0] / 2, canvas[1] / 2)
    view_width = _viewbox_width(master)
    ctx.scale(width * 0.2375 / view_width, width * 0.2375 / view_width)
    points, stops, stroke = logo_style(master)
    ink = cairo.LinearGradient(*points)
    for stop in stops:
        ink.add_color_stop_rgb(*stop)
    ctx.set_source(ink)
    ctx.set_line_width(stroke)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    draw_tree(ctx, master)
    buffer = io.BytesIO()
    surface.write_to_png(buffer)
    buffer.seek(0)
    return Image.open(buffer).convert("RGBA").resize(canvas, Image.Resampling.LANCZOS)


def main():
    spec = load_spec()
    master_path = Path(__file__).with_name("02-logo.svg")
    master = ET.parse(master_path).getroot()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # Render a 2x-supersampled reference logo from the canonical spec width.
    render_width = round(float(spec["width"]) * 4 / 3)
    canvas_width = round(render_width * 0.4)
    canvas_height = round(canvas_width * 448 / 512)
    # Plymouth holds the logo; GNOME's boot-only overlay performs the actual
    # portal reveal over the live login screen in the same compositor.
    logo_image(master, width=render_width, canvas=(canvas_width, canvas_height)).save(
        OUTPUT / "logo.png", optimize=True
    )
    # Both native stages use the same paths, including the filled shared base.
    paths = {element.get("id"): element for element in master.iter()
             if _local_tag(element) == "path"}
    r, g, b = _parse_backdrop(spec["backdrop"])
    duration_seconds = float(spec["durationMs"]) / 1000.0
    origin_x = float(spec["originX"])
    origin_y = float(spec["originY"])
    lines = [
        f"export const DURATION_SECONDS = {duration_seconds:g};",
        f"const WHITE = [{r} / 255, {g} / 255, {b} / 255];",
        f"const PORTAL_ORIGIN_X = {origin_x};",
        f"const PORTAL_ORIGIN_Y = {origin_y};",
    ]
    lines.extend(
        f"const {name.upper()} = {json.dumps(path_commands(paths[name].get('d')))};"
        for name in ("zero", "two")
    )
    lines.append("const TWO_FILLED = " + str(paths["two"].get("stroke") == "none").lower() + ";")
    points, stops, stroke = logo_style(master)
    lines.append(f"const LOGO_INK = {json.dumps([points, stops])};")
    lines.append(f"const LOGO_STROKE_WIDTH = {stroke};")
    generated = "\n".join(lines)
    motion = ROOT / "airootfs/usr/share/gnome-shell/extensions/02-zero-portal@02os/motion.js"
    source = motion.read_text()
    begin, end = "// BEGIN GENERATED LOGO", "// END GENERATED LOGO"
    assert source.count(begin) == source.count(end) == 1
    before, rest = source.split(begin)
    _, after = rest.split(end)
    motion.write_text(before + begin + "\n" + generated + "\n" + end + after)
    assets = list(OUTPUT.glob("*.png"))
    print(f"Generated {len(assets)} PNG assets, {sum(path.stat().st_size for path in assets):,} bytes")


if __name__ == "__main__":
    main()
