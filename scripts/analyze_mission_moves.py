"""
Offline check: did the drone fly where the VLM pointed in the image?

For each finished mission it pairs every move the VLM requested (from
saved_chats/<mission>/history.json) with the telemetry saved with the photo
before and after it (telemetry/<mission>/*.json). The GPS/altitude change is
rotated into the camera frame of the *before* photo (using its yaw and the
camera mounting angle), so it is directly comparable with the VLM's move:
dx = right in the image, dy = toward the image top, dz = up.

Nothing here talks to the drone; it only reads files.

Usage (from the repo root):
    python3 -m scripts.analyze_mission_moves Luna-01
    python3 -m scripts.analyze_mission_moves            # every mission
    python3 -m scripts.analyze_mission_moves --camera-rotation 0 Luna-01

Columns: "dir err" is the angle between commanded and flown horizontal
direction (should be near 0); "ratio" is flown/commanded distance.
"""

import argparse
import json
import math
import re
from pathlib import Path

EARTH_R = 6378137.0
MOVE_RE = re.compile(r"move\((\{.*?\})\)")


def load_moves(history: Path) -> list[tuple[float, float, float]]:
    moves = []
    for msg in json.loads(history.read_text()):
        if msg.get("role") != "assistant":
            continue
        text = " ".join(p.get("data") or "" for p in msg["parts"] if p["type"] == "text")
        found = MOVE_RE.findall(text)
        if found:
            a = json.loads(found[-1])
            moves.append((float(a["dx"]), float(a["dy"]), float(a["dz"])))
    return moves


def load_telemetry(folder: Path) -> list[dict]:
    snaps = []
    for f in sorted(folder.glob("telemetry_*.json")):
        d = json.loads(f.read_text()).get("data", {})
        pos, att = d.get("position") or {}, d.get("attitude") or {}
        snaps.append(dict(lat=pos.get("lat"), lon=pos.get("lon"), alt=pos.get("alt"), yaw=att.get("yaw")))
    return snaps


def flown_in_camera_frame(a: dict, b: dict, cam_rot_deg: float) -> tuple[float, float, float]:
    dn = math.radians(b["lat"] - a["lat"]) * EARTH_R
    de = math.radians(b["lon"] - a["lon"]) * EARTH_R * math.cos(math.radians(a["lat"]))
    top = a["yaw"] + math.radians(cam_rot_deg)  # world bearing of the image top
    up = dn * math.cos(top) + de * math.sin(top)
    right = -dn * math.sin(top) + de * math.cos(top)
    dalt = (b["alt"] - a["alt"]) if None not in (a["alt"], b["alt"]) else float("nan")
    return right, up, dalt


def analyze(mission: str, root: Path, cam_rot: float, min_h: float) -> list[dict]:
    history, tele = root / "saved_chats" / mission / "history.json", root / "telemetry" / mission
    if not history.exists() or not tele.exists():
        return []
    moves, snaps = load_moves(history), load_telemetry(tele)
    if not moves or len(snaps) < 2:
        return []

    print(f"\n=== {mission}: {len(moves)} moves, {len(snaps)} photos ===")
    print(
        f"  {'#':>2} {'cmd dx':>6} {'cmd dy':>6} {'cmd dz':>6} | {'flown dx':>8} {'flown dy':>8} {'flown dz':>8} |"
        f" {'yaw':>5} {'dir err':>7} {'ratio':>5}"
    )
    rows = []
    for i, (dx, dy, dz) in enumerate(moves):
        if i + 1 >= len(snaps):
            break
        a, b = snaps[i], snaps[i + 1]
        if None in (a["lat"], a["lon"], b["lat"], b["lon"], a["yaw"]):
            print(f"  {i + 1:>2} (no GPS/yaw)")
            continue
        fx, fy, fz = flown_in_camera_frame(a, b, cam_rot)
        cmd_h = math.hypot(dx, dy)
        dir_err = ratio = float("nan")
        if cmd_h >= min_h:
            dir_err = (math.degrees(math.atan2(fx, fy) - math.atan2(dx, dy)) + 180) % 360 - 180
            ratio = math.hypot(fx, fy) / cmd_h
        print(
            f"  {i + 1:>2} {dx:6.1f} {dy:6.1f} {dz:6.1f} | {fx:8.1f} {fy:8.1f} {fz:8.1f} |"
            f" {math.degrees(a['yaw']):5.0f} {dir_err:7.0f} {ratio:5.2f}"
        )
        rows.append(dict(dir_err=dir_err, ratio=ratio, dz=dz, fz=fz))
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("missions", nargs="*", help="mission folder names (default: all)")
    p.add_argument("--root", type=Path, default=Path("."), help="folder holding saved_chats/ and telemetry/")
    p.add_argument(
        "--camera-rotation",
        type=float,
        default=180.0,
        help="clockwise angle from the drone's nose to the image top (default 180: top = tail)",
    )
    p.add_argument("--min-horizontal", type=float, default=2.0, help="skip direction stats below this (m)")
    args = p.parse_args(argv)

    chats = args.root / "saved_chats"
    missions = args.missions or (sorted(d.name for d in chats.iterdir() if d.is_dir()) if chats.exists() else [])
    rows = [r for m in missions for r in analyze(m, args.root, args.camera_rotation, args.min_horizontal)]

    horiz = [r for r in rows if not math.isnan(r["ratio"])]
    if horiz:
        errs = sorted(abs(r["dir_err"]) for r in horiz)
        ratios = sorted(r["ratio"] for r in horiz)
        print(f"\nhorizontal moves: {len(horiz)}  median |dir err|: {errs[len(errs) // 2]:.0f} deg  "
              f"median ratio flown/commanded: {ratios[len(ratios) // 2]:.2f}")
    vert = [r for r in rows if abs(r["dz"]) >= 1 and not math.isnan(r["fz"])]
    if vert:
        print("vertical (commanded -> flown):", ", ".join(f"{r['dz']:+.1f}->{r['fz']:+.1f}" for r in vert))
    if not rows:
        print("No missions with moves and telemetry found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
