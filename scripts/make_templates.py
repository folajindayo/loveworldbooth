"""Generate the template clips (A, B, C per template) with WaveSpeed. Run on a workstation, not the booth server.

The flow is still -> pick -> video -> pick, so nothing is animated until its still has been approved:

  python scripts/make_templates.py still men A -n 2          # 2 candidate stills for men/A
  python scripts/make_templates.py pick still men A 1        # approve candidate 1
  python scripts/make_templates.py video men A               # animate the approved still (Kling)
  python scripts/make_templates.py video men C --model seedance
  python scripts/make_templates.py pick video men A 1        # normalise + install as assets/templates/men/A.mp4
  python scripts/make_templates.py status                    # what's approved, money spent so far
  python scripts/make_templates.py still men A --dry-run     # print the request, spend nothing

Prompts and references live in template_studio/plan.json. Clips B and C reuse the approved A still as the
identity reference, so the stand-in is the same person in all three clips. Every generation writes a
.json next to its output (model, prompt, inputs, task id) and a line in raw/generated/ledger.jsonl.
"""
import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.wan_client import WanClient  # noqa: E402

PLAN = json.loads((ROOT / "template_studio/plan.json").read_text())
GEN = ROOT / "raw/generated"
APPROVED = GEN / "approved"
LEDGER = GEN / "ledger.jsonl"
STILL_RES = "2k"


def still_cost(n_images: int) -> float:
    return {"1k": 0.04, "2k": 0.06, "4k": 0.135}[STILL_RES] + 0.002 * n_images


def approved_still(template: str, clip: str) -> Path:
    return APPROVED / f"{template}_{clip}.png"


def next_index(folder: Path, stem: str, ext: str) -> int:
    taken = [int(p.stem.rsplit("_", 1)[1]) for p in folder.glob(f"{stem}_*{ext}") if p.stem.rsplit("_", 1)[1].isdigit()]
    return max(taken, default=0) + 1


def log(entry: dict) -> None:
    GEN.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as fh:
        fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}) + "\n")


async def generate(client: WanClient, model: str, body: dict, out: Path, meta: dict) -> Path | None:
    t0 = time.time()
    task_id = await client.submit_model(model, body)
    print(f"  submitted {out.name}: task {task_id}")
    while True:
        await asyncio.sleep(4)
        try:
            result = await client.poll(task_id)
        except Exception as e:  # transient: keep polling
            print(f"  poll hiccup on {out.name}: {e}")
            continue
        if result.status != "running":
            break
    elapsed = round(time.time() - t0)
    record = {**meta, "model": model, "body": body, "task_id": task_id, "status": result.status,
              "error": result.error, "seconds": elapsed, "output": str(out.relative_to(ROOT))}
    log(record)
    if result.status != "completed":
        print(f"  FAILED {out.name} after {elapsed}s: {result.error}")
        return None
    await client.download(result.outputs[0], out)
    out.with_suffix(".json").write_text(json.dumps(record, indent=2))
    print(f"  done {out.name} in {elapsed}s -> {out}")
    return out


async def cmd_still(args) -> None:
    clip = PLAN["clips"][args.clip]
    refs = []
    if "identity_from" in clip:
        identity = approved_still(args.template, clip["identity_from"])
        if not identity.exists():
            sys.exit(f"Approve {args.template} {clip['identity_from']} first: it carries the stand-in's identity.")
        refs.append(identity)
    refs += [ROOT / r for r in clip["refs"]]
    missing = [str(r) for r in refs if not r.exists()]
    if missing:
        sys.exit(f"Missing reference files: {missing}")
    prompt = clip["still_prompt"].replace("{stand_in}", PLAN["stand_ins"][args.template])
    cost = still_cost(len(refs)) * args.n
    print(f"{args.template} {args.clip}: {args.n} still(s), {len(refs)} refs, est ${cost:.2f}")
    if args.dry_run:
        print(json.dumps({"model": PLAN["still_model"], "prompt": prompt, "refs": [str(r) for r in refs]}, indent=2))
        return
    client = WanClient()
    try:
        urls = [await client.upload(r) for r in refs]
        body = {"prompt": prompt, "images": urls, "aspect_ratio": "9:16", "resolution": STILL_RES, "output_format": "png"}
        folder = GEN / "stills"
        folder.mkdir(parents=True, exist_ok=True)
        start = next_index(folder, f"{args.template}_{args.clip}", ".png")
        meta = {"kind": "still", "template": args.template, "clip": args.clip, "est_usd": round(still_cost(len(refs)), 3)}
        await asyncio.gather(*(
            generate(client, PLAN["still_model"], body, folder / f"{args.template}_{args.clip}_{start + i}.png", meta)
            for i in range(args.n)))
    finally:
        await client.close()


async def cmd_video(args) -> None:
    still = approved_still(args.template, args.clip)
    if not still.exists():
        sys.exit(f"Approve a still for {args.template} {args.clip} first.")
    spec = PLAN["video_models"][args.model]
    body_extra = dict(spec["body"])
    cost = spec["usd_per_s"] * body_extra.get("duration", 5) * args.n
    print(f"{args.template} {args.clip}: {args.n} video(s) on {args.model}, est ${cost:.2f}")
    if args.dry_run:
        print(json.dumps({"model": spec["model"], "prompt": PLAN["clips"][args.clip]["motion_prompt"], **body_extra}, indent=2))
        return
    client = WanClient()
    try:
        body = {"image": await client.upload(still), "prompt": PLAN["clips"][args.clip]["motion_prompt"], **body_extra}
        folder = GEN / "videos"
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{args.template}_{args.clip}_{args.model}"
        start = next_index(folder, stem, ".mp4")
        meta = {"kind": "video", "template": args.template, "clip": args.clip,
                "est_usd": round(spec["usd_per_s"] * body_extra.get("duration", 5), 3)}
        await asyncio.gather(*(generate(client, spec["model"], body, folder / f"{stem}_{start + i}.mp4", meta)
                               for i in range(args.n)))
    finally:
        await client.close()


def cmd_pick(args) -> None:
    APPROVED.mkdir(parents=True, exist_ok=True)
    if args.kind == "still":
        src = GEN / "stills" / f"{args.template}_{args.clip}_{args.index}.png"
        if not src.exists():
            sys.exit(f"No such still: {src}")
        shutil.copyfile(src, approved_still(args.template, args.clip))
        print(f"approved {src.name} as {args.template} {args.clip}")
        return
    matches = sorted((GEN / "videos").glob(f"{args.template}_{args.clip}_*_{args.index}.mp4"))
    if len(matches) != 1:
        sys.exit(f"Expected one video for index {args.index}, found {[m.name for m in matches]}. "
                 "Use the full number from the file name.")
    template = config.load_templates()[args.template]
    dest = config.template_clip(template, args.clip)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Normalise to the template spec: 720x1280, 30 fps, exactly 5 s, H.264, no audio.
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(matches[0]),
                    "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps=30",
                    "-t", "5", "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
                    str(dest)], check=True)
    shutil.copyfile(matches[0], APPROVED / f"{args.template}_{args.clip}.mp4")
    print(f"installed {matches[0].name} -> {dest}")


def cmd_status(_args) -> None:
    for tid in config.load_templates():
        row = []
        for clip in config.CLIPS:
            s = "still✓" if approved_still(tid, clip).exists() else "still·"
            v = "video✓" if (APPROVED / f"{tid}_{clip}.mp4").exists() else "video·"
            row.append(f"{clip}: {s} {v}")
        print(f"{tid:8s} " + " | ".join(row))
    spent = 0.0
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            e = json.loads(line)
            spent += e.get("est_usd", 0) if e.get("status") == "completed" else 0
    print(f"estimated spend so far: ${spent:.2f} (completed generations; see {LEDGER.relative_to(ROOT)})")


def main() -> None:
    if not config.WAVESPEED_API_KEY:
        sys.exit("WAVESPEED_API_KEY is not set in .env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("still", "video"):
        p = sub.add_parser(name)
        p.add_argument("template")
        p.add_argument("clip", choices=config.CLIPS)
        p.add_argument("-n", type=int, default=2 if name == "still" else 1, help="candidates to generate")
        p.add_argument("--dry-run", action="store_true")
        if name == "video":
            p.add_argument("--model", default="kling", choices=list(PLAN["video_models"]))
    p = sub.add_parser("pick")
    p.add_argument("kind", choices=["still", "video"])
    p.add_argument("template")
    p.add_argument("clip", choices=config.CLIPS)
    p.add_argument("index", type=int)
    sub.add_parser("status")
    args = ap.parse_args()
    if getattr(args, "template", None) and args.template not in config.load_templates():
        sys.exit(f"Unknown template {args.template}; see assets/templates.json")
    if args.cmd == "still":
        asyncio.run(cmd_still(args))
    elif args.cmd == "video":
        asyncio.run(cmd_video(args))
    elif args.cmd == "pick":
        cmd_pick(args)
    else:
        cmd_status(args)


if __name__ == "__main__":
    main()
