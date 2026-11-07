#!/usr/bin/env python3
"""Generate the agent-teams Wardley map (SVG).

Usage:
    python3 scripts/wardley_agent_teams.py [--out docs/images/wardley-agent-teams.svg]

The map is a strategic snapshot: the value chain (y: user need → infrastructure)
against evolution (x: genesis → commodity). Orange nodes are Sugar's build bets;
blue nodes are standards to adopt; grey nodes are external/commodity capabilities.

Arrows point from a component to the capability it enables (upward). Dashed red
arrows mark capabilities that are commoditising.
"""
from __future__ import annotations

import argparse
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1440, 900
PLOT = {"left": 70, "right": 1400, "top": 96, "bottom": 826}

STAGES = [
    (0.00, 0.17, "Genesis", "#fbf7f1"),
    (0.17, 0.35, "Custom-built", "#eef6f8"),
    (0.35, 0.65, "Product", "#ecf7f0"),
    (0.65, 1.00, "Commodity / utility", "#f3f3f3"),
]

COLORS = {
    "need": ("#ffffff", "#1f2937", "#111827", "bold", ""),
    "bet": ("#ffedd5", "#ea580c", "#7c2d12", "bold", ""),
    "product": ("#dcfce7", "#16a34a", "#14532d", "normal", ""),
    "standard": ("#dbeafe", "#2563eb", "#1e3a8a", "normal", ""),
    "external": ("#f3f4f6", "#6b7280", "#374151", "normal", "6 3"),
    "commodity": ("#f3f4f6", "#9ca3af", "#4b5563", "normal", ""),
}

# id: (label, evolution 0..1, visibility 0..1, kind)
NODES = {
    # User needs
    "u1": ("Ship software with agent crews", 0.10, 0.95, "need"),
    "u2": ("See & steer agent work", 0.30, 0.92, "need"),
    "u3": ("Prove who did what (audit)", 0.10, 0.88, "need"),
    "u4": ("Run agents on my own infra", 0.26, 0.86, "need"),
    # Surfaces
    "ui": ("Mission Control UI\n(web + macOS)", 0.55, 0.78, "product"),
    "cli": ("CLI & scripts", 0.72, 0.76, "product"),
    "kanban": ("Kanban UI conventions", 0.80, 0.80, "commodity"),
    "contract": ("Sugar contract\n(REST/SSE + MCP)", 0.50, 0.68, "standard"),
    # Sugar build bets
    "ledger": ("Durable ledger\n+ atomic claims", 0.28, 0.62, "bet"),
    "deps": ("Dependencies & scheduling", 0.12, 0.57, "bet"),
    "lease": ("Leases, heartbeat & reaper", 0.18, 0.52, "bet"),
    "prov": ("Provenance & audit trail", 0.10, 0.46, "bet"),
    "events": ("Event spine /\nactivity history", 0.32, 0.48, "bet"),
    "registry": ("Agent registry & heartbeats", 0.09, 0.39, "bet"),
    # Supporting product
    "memory": ("Agent memory store\n(Sugar memory)", 0.44, 0.42, "product"),
    "mcp": ("MCP (tool protocol)", 0.68, 0.58, "standard"),
    "a2a": ("A2A (agent-to-agent)", 0.57, 0.55, "standard"),
    # External / competitors
    "single": ("Single-harness team features\n(Claude Agent Teams, Vibe Kanban)", 0.56, 0.50, "external"),
    "harness": ("Agent harnesses\n(Claude Code, Codex,\nGoose, Hermes, DSH)", 0.64, 0.38, "external"),
    "orchestrators": ("Orchestration frameworks\n(LangGraph, CrewAI, SDKs)", 0.62, 0.30, "external"),
    # Commodity / utility
    "models": ("Foundation models / inference", 0.90, 0.16, "commodity"),
    "git": ("Git hosting & code review", 0.87, 0.30, "commodity"),
    "auth": ("Auth / OIDC / tokens", 0.77, 0.22, "commodity"),
    "obs": ("Observability & cost telemetry", 0.72, 0.17, "commodity"),
    "store": ("Storage (SQLite / Postgres)", 0.80, 0.11, "commodity"),
    "hosting": ("Cloud / self-host infra", 0.94, 0.07, "commodity"),
    "email": ("Email / billing / support", 0.70, 0.03, "commodity"),
}

# child -> [parents it enables]  (arrow points child -> parent)
LINKS = {
    "ui": ["u1", "u2"],
    "cli": ["u1"],
    "kanban": ["ui"],
    "contract": ["ui", "cli", "u2"],
    "mcp": ["contract"],
    "a2a": ["contract"],
    "ledger": ["u1", "u4", "contract"],
    "deps": ["ledger"],
    "lease": ["ledger", "events"],
    "prov": ["u3", "events"],
    "events": ["u2", "u3"],
    "registry": ["u4", "lease"],
    "memory": ["u1"],
    "single": ["u1"],
    "harness": ["u1", "u4", "contract"],
    "orchestrators": ["harness"],
    "models": ["harness"],
    "git": ["harness"],
    "auth": ["contract", "ui"],
    "obs": ["events"],
    "store": ["ledger", "prov", "events", "memory"],
    "hosting": ["store", "auth", "obs"],
    "email": ["hosting"],
}

# dashed red movement arrows: (from_x, from_y, to_x, to_y, label)
MOVEMENT = [
    (0.665, 0.50, 0.775, 0.50, "commoditising"),
    (0.775, 0.38, 0.885, 0.38, "commoditising"),
    (0.70, 0.51, 0.80, 0.51, "standardising"),
]

WRAP = 34
MAX_LINES = 3


def x_px(evolution: float) -> float:
    return PLOT["left"] + evolution * (PLOT["right"] - PLOT["left"])


def y_px(visibility: float) -> float:
    return PLOT["top"] + (1 - visibility) * (PLOT["bottom"] - PLOT["top"])


def label_lines(label: str) -> list[str]:
    lines: list[str] = []
    for part in label.split("\n"):
        lines.extend(textwrap.wrap(part, WRAP) or [part])
    return lines[:MAX_LINES]


def node_size(label: str) -> tuple[float, float, list[str]]:
    lines = label_lines(label)
    width = max(len(line) for line in lines) * 6.6 + 24
    height = 27 + 15 * (len(lines) - 1)
    return max(width, 90), height, lines


def render() -> str:
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="-apple-system, BlinkMacSystemFont, '
        f'Segoe UI, Helvetica, Arial, sans-serif">'
    )
    parts.append(
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#9ca3af"/></marker>'
        '<marker id="move" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#dc2626"/></marker>'
        '</defs>'
    )
    parts.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

    # Stage bands
    for start, end, label, fill in STAGES:
        x1, x2 = x_px(start), x_px(end)
        parts.append(
            f'<rect x="{x1:.1f}" y="{PLOT["top"]}" width="{x2 - x1:.1f}" '
            f'height="{PLOT["bottom"] - PLOT["top"]}" fill="{fill}"/>'
        )
        parts.append(
            f'<text x="{(x1 + x2) / 2:.1f}" y="{PLOT["top"] - 12}" text-anchor="middle" '
            f'font-size="12" font-weight="600" fill="#6b7280" letter-spacing="0.08em">'
            f'{label.upper()}</text>'
        )

    # Grid
    for i in range(21):
        evo = i / 20
        x = x_px(evo)
        parts.append(
            f'<line x1="{x:.1f}" y1="{PLOT["top"]}" x2="{x:.1f}" y2="{PLOT["bottom"]}" '
            f'stroke="#d1d5db" stroke-width="0.5" opacity="0.6"/>'
        )
    for i in range(11):
        vis = i / 10
        y = y_px(vis)
        parts.append(
            f'<line x1="{PLOT["left"]}" y1="{y:.1f}" x2="{PLOT["right"]}" y2="{y:.1f}" '
            f'stroke="#d1d5db" stroke-width="0.5" opacity="0.6"/>'
        )

    # Axes
    parts.append(
        f'<line x1="{PLOT["left"]}" y1="{PLOT["bottom"]}" x2="{PLOT["right"]}" '
        f'y2="{PLOT["bottom"]}" stroke="#374151" stroke-width="1.2"/>'
    )
    parts.append(
        f'<line x1="{PLOT["left"]}" y1="{PLOT["top"]}" x2="{PLOT["left"]}" '
        f'y2="{PLOT["bottom"]}" stroke="#374151" stroke-width="1.2"/>'
    )
    parts.append(
        f'<text x="{(PLOT["left"] + PLOT["right"]) / 2:.1f}" y="{PLOT["bottom"] + 46}" '
        f'text-anchor="middle" font-size="14" font-weight="600" fill="#374151">'
        f'Evolution →</text>'
    )
    parts.append(
        f'<text x="26" y="{(PLOT["top"] + PLOT["bottom"]) / 2:.1f}" text-anchor="middle" '
        f'font-size="14" font-weight="600" fill="#374151" '
        f'transform="rotate(-90 26 {(PLOT["top"] + PLOT["bottom"]) / 2:.1f})">'
        f'Value chain visibility (user need → infrastructure)</text>'
    )

    # Build-bet highlight
    bx1, bx2 = x_px(0.03), x_px(0.37)
    by1, by2 = y_px(0.68), y_px(0.33)
    parts.append(
        f'<rect x="{bx1:.1f}" y="{by1:.1f}" width="{bx2 - bx1:.1f}" '
        f'height="{by2 - by1:.1f}" rx="14" fill="#fff7ed" opacity="0.55" '
        f'stroke="#fdba74" stroke-width="1" stroke-dasharray="5 4"/>'
    )
    parts.append(
        f'<text x="{(bx1 + bx2) / 2:.1f}" y="{by1 + 20:.1f}" text-anchor="middle" '
        f'font-size="11.5" font-weight="700" fill="#c2410c">'
        f"SUGAR'S BUILD BET — coordination semantics</text>"
    )

    positions: dict[str, tuple[float, float, float, float]] = {}
    for node_id, (label, evo, vis, kind) in NODES.items():
        width, height, _lines = node_size(label)
        positions[node_id] = (x_px(evo), y_px(vis), width, height)

    # Links first (behind nodes)
    for child, parents in LINKS.items():
        cx, cy, cw, ch = positions[child]
        for parent in parents:
            px, py, pw, ph = positions[parent]
            # start at top edge of child, end at bottom edge of parent
            x1, y1 = cx, cy - ch / 2
            x2, y2 = px, py + ph / 2
            if abs(py - cy) > ch / 2 + ph / 2:
                mid_y = (y1 + y2) / 2
                parts.append(
                    f'<path d="M{x1:.1f},{y1:.1f} L{x1:.1f},{mid_y:.1f} L{x2:.1f},{mid_y:.1f} '
                    f'L{x2:.1f},{y2:.1f}" fill="none" stroke="#9ca3af" stroke-width="1.1" '
                    f'opacity="0.75" marker-end="url(#arrow)"/>'
                )

    # Nodes
    for node_id, (label, evo, vis, kind) in NODES.items():
        cx, cy, width, height = positions[node_id]
        fill, stroke, text, weight, dash = COLORS[kind]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(
            f'<rect x="{cx - width / 2:.1f}" y="{cy - height / 2:.1f}" width="{width:.1f}" '
            f'height="{height:.1f}" rx="7" fill="{fill}" stroke="{stroke}" stroke-width="1.4"'
            f'{dash_attr}/>'
        )
        lines = label_lines(label)
        line_height = 15
        for index, line in enumerate(lines):
            dy = cy + 4.5 + (index - (len(lines) - 1) / 2) * line_height
            parts.append(
                f'<text x="{cx:.1f}" y="{dy:.1f}" text-anchor="middle" font-size="11.5" '
                f'font-weight="{weight}" fill="{text}">{escape(line)}</text>'
            )

    # Movement arrows
    for fx, fy, tx, ty, label in MOVEMENT:
        x1, y1, x2, y2 = x_px(fx), y_px(fy), x_px(tx), y_px(ty)
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#dc2626" stroke-width="1.6" stroke-dasharray="6 4" '
            f'marker-end="url(#move)" opacity="0.85"/>'
        )
        parts.append(
            f'<text x="{(x1 + x2) / 2:.1f}" y="{y1 - 6:.1f}" text-anchor="middle" '
            f'font-size="10.5" font-style="italic" fill="#b91c1c">{label}</text>'
        )

    # Title and legend
    parts.append(
        f'<text x="{PLOT["left"]}" y="38" font-size="21" font-weight="700" fill="#111827">'
        f'Sugar for agent teams — Wardley map</text>'
    )
    parts.append(
        f'<text x="{PLOT["left"]}" y="58" font-size="12" fill="#6b7280">'
        f'Where to build (orange), where to adopt (blue), what is commoditising (red arrows). '
        f'Draft, 2026-11-06.</text>'
    )

    legend = [
        ("bet", "Sugar build bet"),
        ("standard", "Adopt / standard"),
        ("product", "Supporting product"),
        ("external", "External / competitor"),
        ("commodity", "Commodity"),
    ]
    lx = PLOT["right"] - 700
    for index, (kind, label) in enumerate(legend):
        fill, stroke, _text, _weight, _dash = COLORS[kind]
        offset = index * 140
        parts.append(
            f'<rect x="{lx + offset}" y="32" width="13" height="13" rx="3" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.3"/>'
        )
        parts.append(
            f'<text x="{lx + offset + 18}" y="43" font-size="11" fill="#4b5563">{label}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent.parent / "docs/images/wardley-agent-teams.svg"),
    )
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
