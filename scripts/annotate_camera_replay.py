#!/usr/bin/env python3
"""Create local JSONL passage labels from original video pixels.

Controls: A annotates one person (select a face; body and torso are optional), N
keeps the frame and advances, Q saves and exits. Repeat A for other people in a
frame. The tool writes annotations only; it never copies or uploads video frames.
Use local pseudonyms for passage/identity labels; do not enter student records.
"""
import argparse
import json
from pathlib import Path

import cv2


def select_box(frame, title):
    x, y, width, height = cv2.selectROI(title, frame, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(title)
    if width <= 0 or height <= 0:
        return None
    return [int(x), int(y), int(x + width), int(y + height)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--every", type=int, default=3, help="annotate every Nth decoded frame")
    parser.add_argument("--max-frames", type=int, default=0, help="0 means no limit")
    args = parser.parse_args()
    if args.every < 1 or args.max_frames < 0:
        parser.error("--every must be positive and --max-frames cannot be negative")
    if not args.video.is_file():
        parser.error("video file does not exist")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        parser.error("could not open local video")
    frame_index, saved, labels = 0, 0, []
    window = "CBVMS local annotation · original pixels · A annotate / N next / Q quit"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    try:
        while args.max_frames == 0 or saved < args.max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            current = frame_index
            frame_index += 1
            if current % args.every:
                continue
            timestamp_ms = int(capture.get(cv2.CAP_PROP_POS_MSEC))
            people = []
            while True:
                display = frame.copy()
                for person in people:
                    x1, y1, x2, y2 = person["face_box"] or person["body_box"]
                    cv2.rectangle(display, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(display, person["passage_alias"], (x1, max(16, y1 - 5)),
                                cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 0), 1)
                cv2.imshow(window, display)
                key = cv2.waitKey(0) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key in (ord("n"), ord(" ")):
                    labels.append({"frame_index": current, "timestamp_ms": timestamp_ms,
                                   "people": people})
                    saved += 1
                    break
                if key != ord("a"):
                    continue
                face = select_box(frame, "Select face; Enter accepts, Esc skips")
                body = select_box(frame, "Select associated person body (Esc skips)")
                if face is None and body is None:
                    continue
                torso = (select_box(frame, "Select visible shirt/torso region (Esc skips)")
                         if body is not None else None)
                alias = input("Passage alias (reuse this short pseudonym across frames): ").strip()
                if not alias:
                    print("Passage alias is required; person not added.")
                    continue
                identity = input("Expected identity alias (__unknown__ for known outsider; blank if unavailable): ").strip()
                uniform = input("Uniform label [correct_uniform/wrong_uniform/unable]: ").strip().lower()
                if uniform not in {"correct_uniform", "wrong_uniform", "unable"}:
                    print("Invalid label; use correct_uniform, wrong_uniform, or unable.")
                    continue
                people.append({"passage_alias": alias, "identity_label": identity or None,
                    "uniform_label": uniform, "face_box": face, "body_box": body,
                    "torso_box": torso})
            if key in (ord("q"), 27):
                break
    finally:
        capture.release()
        cv2.destroyAllWindows()
    with args.output.open("w", encoding="utf-8") as stream:
        for row in labels:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps({"frames_annotated": len(labels), "people_annotated": sum(len(r["people"]) for r in labels),
                      "output": str(args.output), "images_written": 0, "upload": False}, sort_keys=True))


if __name__ == "__main__":
    main()
