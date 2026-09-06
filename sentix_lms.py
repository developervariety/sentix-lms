#!/usr/bin/env python3
"""
sentix-lms — automate a Sentix LMS (SambaSafety) training account.

Log in, list the lessons on the account, and complete any lesson from start to
finish: it plays through the video pages (recording the watch time each page
requires), answers the knowledge-check questions, and finalizes the last page so
the lesson is marked complete (which unlocks the next available lessons).
Downloading the lesson videos and transcribing the narration are optional.

It is meant for completing, archiving, and reviewing training on an account you
own. Each run opens its own fresh, isolated session, so two runs never step on
each other's progress.

Authentication (choose one):
  --landing-url URL        the one-click auto-login link              (env SENTIX_LANDING_URL)
  --uid UID --coid COID    your User ID and Company ID               (env SENTIX_UID / SENTIX_COID)

Subcommands:
  scan                     list assigned and available lessons
  run                      complete one or more lessons; optional --download / --transcribe

Examples:
  python3 sentix_lms.py scan
  python3 sentix_lms.py run --lesson 12345
  python3 sentix_lms.py run --assigned --transcribe
  python3 sentix_lms.py run --all --download --out ./output
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

BASE = "https://www.sentixlms.com"
# base64("t2/lessonmenu.lasso") — the lesson menu include the site uses.
LESSON_MENU = "menu.lasso?INC=dDIvbGVzc29ubWVudS5sYXNzbw=="
UA = "Mozilla/5.0"


class Session:
    """A single isolated LMS session backed by its own throwaway cookie jar."""

    def __init__(self):
        fh = tempfile.NamedTemporaryFile(prefix="sentix_ck_", delete=False)
        fh.close()
        self.jar = fh.name
        self.coid = None

    def close(self):
        try:
            os.unlink(self.jar)
        except OSError:
            pass

    def req(self, path, data=None, follow=False, out=None, timeout=60):
        url = path if path.startswith("http") else f"{BASE}/{path}"
        cmd = ["curl", "-s", "--max-time", str(timeout), "-A", UA, "-c", self.jar, "-b", self.jar]
        if follow:
            cmd += ["-L", "-e", f"{BASE}/player.lasso"]
        if data is not None:
            cmd += ["-X", "POST", "--data", data]
        if out:
            cmd += ["-o", out]
        cmd += [url]
        return subprocess.run(cmd, capture_output=True).stdout.decode("utf-8", "ignore")


# --------------------------------------------------------------------------- #
# Auth + lesson listing
# --------------------------------------------------------------------------- #
def login(sess, landing=None, uid=None, coid=None):
    if landing:
        sess.req(landing, follow=True, out=os.devnull)
        m = re.search(r"coid%3d(\d+)", landing, re.I) or re.search(r"[?&]coid=(\d+)", landing, re.I)
        sess.coid = m.group(1) if m else None
    elif uid and coid:
        sess.req("?F=login", out=os.devnull)  # prime the session cookies
        sess.req("index.lasso", data=f"F=login&UID={uid}&CoID={coid}", follow=True, out=os.devnull)
        sess.coid = coid
    else:
        sys.exit("Provide --landing-url OR --uid and --coid (or the SENTIX_* env vars).")

    menu = sess.req(LESSON_MENU)
    if "player.lasso?Lesson=" not in menu:
        sys.exit("Login failed — check your landing link / User ID + Company ID.")
    return menu


def _links(html):
    out, seen = [], set()
    for m in re.finditer(r"player\.lasso\?Lesson=(\d+)&LessonName=([^\"]+)", html):
        lid = m.group(1)
        if lid in seen:
            continue
        seen.add(lid)
        name = (m.group(2).replace("%20", " ").replace("%2f", "/")
                .replace("%3f", "?").replace("%2c", ",").strip())
        out.append((lid, name))
    return out


def scan(sess, menu=None):
    """Return (assigned, available) lesson lists of (id, name)."""
    menu = menu or sess.req(LESSON_MENU)
    parts = re.split(r"Available Lessons", menu, maxsplit=1)
    assigned = _links(parts[0])
    available = _links(parts[1]) if len(parts) > 1 else []
    aset = {i for i, _ in assigned}
    available = [(i, n) for i, n in available if i not in aset]
    return assigned, available


def with_session(auth, fn):
    """Run `fn(session)` inside a fresh, logged-in, auto-closed session."""
    s = Session()
    try:
        login(s, *auth)
        return fn(s)
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# Player-page parsing + the page-walk
# --------------------------------------------------------------------------- #
_PAGE = {
    "page": re.compile(r"ThisPage: (\d+)"),
    "type": re.compile(r"Type: ([A-Z]+)"),
    "file": re.compile(r"Filename: ([^\n<]+)"),
    "total": re.compile(r"TotalPages[ :=]+(\d+)"),
    "token": re.compile(r'id="nextPage"[^>]*action="[^"]*LessonLoad:([A-Z0-9]+)"'),
    "coid": re.compile(r'id="CoID"[^>]*value="(\d+)"'),
}


def parse(html):
    d = {k: (r.search(html).group(1) if r.search(html) else None) for k, r in _PAGE.items()}
    d["rids"] = re.findall(r"ResponseID=(\d+)", html)
    d["html"] = html
    if d["file"]:
        d["file"] = d["file"].strip()
    return d


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).strip()


def parse_question(html):
    """Return (question_text, {ResponseID: option_text}) for a question page."""
    options = {}
    for m in re.finditer(r'<a[^>]*onClick="[^"]*ResponseID=(\d+)[^"]*"[^>]*>(.*?)</a>', html, re.S | re.I):
        options[m.group(1)] = _text(m.group(2))
    m = re.search(r'class="QuestionWrapper"[^>]*>(.*?)<a[^>]*ResponseID', html, re.S | re.I)
    return (_text(m.group(1)) if m else ""), options


def video_duration(path, default=300):
    try:
        import av
        return round(av.open(path).duration / 1_000_000)
    except Exception:
        return default


def answer_question(sess, lesson, rids, known):
    """Answer the current question, returning (rid, clean).

    Submitting a *wrong* option locks the question for the rest of that session
    (remediation), so a correct answer only advances the page in a session that
    has not tried a wrong one. To respect that:

      * If a known-correct option (from an earlier discovery) is among the
        current options, submit only it — a clean answer that advances.
      * Otherwise DISCOVER the correct option by trying each. That locks this
        session's question, but the correct id is remembered in `known`, so the
        next fresh session (see `run_lesson`) can answer it cleanly.

    `clean` is True only in the first case.
    """
    hit = next((r for r in rids if r in known), None)
    if hit:
        sess.req("lib/response.lasso", data=f"ResponseID={hit}&Lesson={lesson}")
        return hit, True
    for rid in rids:
        r = sess.req("lib/response.lasso", data=f"ResponseID={rid}&Lesson={lesson}").lower()
        if "incorrect" not in r and "correct" in r:
            known.add(rid)
            return rid, False
    return None, False


def get_page(sess, lesson):
    """Fetch and parse the player's current page for a lesson."""
    return parse(sess.req(f"player.lasso?Lesson={lesson}"))


def fetch_video(sess, lesson, filename, out_dir, keep):
    """Download a page's video and return (page_time, kept_path_or_None).

    The file is fetched either way (its length sets the required watch time); it
    is kept in `out_dir` only when `keep` is set, otherwise fetched to a temp
    file that is removed once its duration has been read.
    """
    if keep:
        path = os.path.join(out_dir, f"{lesson}_{filename}")
        if not os.path.exists(path) or os.path.getsize(path) < 10000:
            sess.req(f"mobile/{lesson}/{filename}", out=path)
        return video_duration(path) + 5, path
    fh = tempfile.NamedTemporaryFile(prefix="sentix_vid_", suffix=".mp4", delete=False)
    fh.close()
    sess.req(f"mobile/{lesson}/{filename}", out=fh.name)
    page_time = video_duration(fh.name) + 5
    try:
        os.unlink(fh.name)
    except OSError:
        pass
    return page_time, None


def submit_next(sess, lesson, cur, page_time, coid):
    """Record the current page's time and POST the navigation off it.

    A fresh token is read right before navigating, because answering a question
    rotates the navigation token. On the final page this same POST is what marks
    the lesson complete (its `lessonend` flag is set), which is what unlocks the
    next available lessons.
    """
    token = get_page(sess, lesson)["token"]
    sess.req("lib/updatelessoninfo.lasso", data=f"CoID={coid}&Time={page_time}")
    sess.req("lib/clearRedirect.lasso", data="")
    sess.req(f"player.lasso?-session=LessonLoad:{token}",
             data=f"Lesson={lesson}&Page={cur + 1}&review=&production=&LastPageTime={page_time}&CC=",
             out=os.devnull)


def advance(sess, lesson, cur, page_time, coid, tries=8):
    """Move off the current page and return the parsed next page.

    The navigation POST can 302 to the site's error page even when it succeeds,
    and the saved progress takes a moment to commit — so the authoritative state
    is a fresh GET of the player, retried until the page number actually moves
    past `cur` (or the tries run out).
    """
    submit_next(sess, lesson, cur, page_time, coid)
    p = {"page": None}
    for _ in range(tries):
        p = get_page(sess, lesson)
        if p["page"] and int(p["page"]) > cur:
            return p
        time.sleep(1)
    return p


def walk(sess, lesson, coid, known, out_dir=None, keep=False, qa=None, max_pages=80):
    """Walk one lesson to the end, auto-accepting every page.

    Returns (kept_videos, reached_end, last_page). A video's file is fetched to
    read its length even without `keep`; it is only saved to `out_dir` when
    `keep` is set. When `qa` is a dict, each question's text, options and the
    correct answer are recorded into it (keyed by the correct ResponseID).
    """
    if keep:
        os.makedirs(out_dir, exist_ok=True)
    p = get_page(sess, lesson)
    coid = coid or p["coid"] or "0"
    videos, visited = [], set()
    last_page, reached_end = 0, False

    for _ in range(max_pages):
        if not p["page"]:
            print(f"  lesson {lesson}: no page data (session expired?)")
            break
        cur, total = int(p["page"]), int(p["total"] or 1)
        last_page = cur
        if cur in visited:
            print(f"  lesson {lesson}: progress not advancing at page {cur}; stopping")
            break
        visited.add(cur)

        page_time = 30
        if p["type"] == "VIDEO" and p["file"]:
            page_time, kept = fetch_video(sess, lesson, p["file"], out_dir, keep)
            if kept:
                videos.append((p["file"], kept))
            print(f"  page {cur}/{total} [VIDEO] {p['file']}")
        elif p["type"] == "QUESTION":
            rid, clean = answer_question(sess, lesson, p["rids"], known)
            if qa is not None and rid and rid not in qa:
                stem, options = parse_question(p["html"])
                if options:
                    qa[rid] = (stem, options.get(rid, ""), options)
            print(f"  page {cur}/{total} [QUESTION] answered={rid}"
                  f"{'' if clean else ' (discovered — will apply next pass)'}")
            if rid and not clean:
                # This session's question is now locked; stop so a fresh session
                # can answer it cleanly with the id we just learned.
                break
        else:
            print(f"  page {cur}/{total} [{p['type']}]")

        if cur >= total:
            # Submit the final page (its lessonend flag is set) to mark the
            # lesson complete — this is what unlocks the next available lessons.
            submit_next(sess, lesson, cur, page_time, coid)
            reached_end = True
            print(f"  lesson {lesson}: reached the end ({cur}/{total}); marked complete")
            break

        p = advance(sess, lesson, cur, page_time, coid)
        if not p["page"] or int(p["page"]) <= cur:
            print(f"  lesson {lesson}: stalled at page {cur}")
            break
    return videos, reached_end, last_page


def transcribe(videos, model_name="small"):
    from faster_whisper import WhisperModel
    print(f"  transcribing {len(videos)} video(s) with faster-whisper '{model_name}'...")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    for filename, path in videos:
        txt = path + ".txt"
        if os.path.exists(txt) and os.path.getsize(txt) > 0:
            continue
        try:
            segs, _ = model.transcribe(path, vad_filter=True, language="en")
            lines = "\n".join(f"[{s.start:7.1f}] {s.text.strip()}" for s in segs)
        except Exception as e:
            # A video with no/undecodable audio track shouldn't kill the batch.
            print(f"    -> {os.path.basename(path)}: skipped ({e})")
            continue
        with open(txt, "w") as f:
            f.write(f"# {filename}\n" + lines + "\n")
        print(f"    -> {os.path.basename(txt)}")


def write_questions(lesson, name, qa, out_dir):
    """Save a lesson's questions and correct answers to a text file."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{lesson}_questions.txt")
    with open(path, "w") as f:
        f.write(f"# {name} (lesson {lesson}) — {len(qa)} question(s)\n")
        for i, (rid, (stem, answer, options)) in enumerate(qa.items(), 1):
            f.write(f"\nQ{i}. {stem}\n")
            for oid, text in options.items():
                f.write(f"  [{'*' if oid == rid else ' '}] {text}\n")
            f.write(f"  Answer: {answer}\n")
    print(f"  wrote {path} ({len(qa)} question(s))")


def run_lesson(auth, lesson, name, out_dir, keep, known, save_questions=False, attempts=40):
    """Walk a lesson to completion, re-opening a fresh session between attempts.

    Progress is saved server-side, so each attempt resumes where the last left
    off. A fresh session also lets a question that was locked while its answer
    was being discovered be answered cleanly (see `answer_question`), so the
    frontier advances a little on every pass until the lesson is finished.
    Returns the list of kept videos.
    """
    print(f"Lesson {lesson}: {name}")
    videos, prev = [], -1
    qa = {} if save_questions else None
    for _ in range(attempts):
        v, reached, page = with_session(
            auth, lambda s: walk(s, lesson, s.coid, known, out_dir=out_dir, keep=keep, qa=qa))
        videos += v
        if reached or page <= prev:
            if not reached and page <= prev:
                print(f"  lesson {lesson}: no further progress past page {page}; moving on")
            break
        prev = page
    if qa:
        write_questions(lesson, name, qa, out_dir)
    return videos


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def add_auth(ap):
    ap.add_argument("--landing-url", default=os.environ.get("SENTIX_LANDING_URL"),
                    help="one-click auto-login link (or env SENTIX_LANDING_URL)")
    ap.add_argument("--uid", default=os.environ.get("SENTIX_UID"),
                    help="User ID (or env SENTIX_UID)")
    ap.add_argument("--coid", default=os.environ.get("SENTIX_COID"),
                    help="Company ID (or env SENTIX_COID)")


def main():
    ap = argparse.ArgumentParser(description="Automate a Sentix LMS training account.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="list assigned and available lessons")
    add_auth(s)
    s.add_argument("pattern", nargs="?", help="only show lessons whose name contains this (case-insensitive)")

    r = sub.add_parser("run", help="complete lessons (auto-accept); optionally download / transcribe")
    add_auth(r)
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--lesson", help="a single lesson id")
    g.add_argument("--name", help="complete lessons whose name contains this (case-insensitive)")
    g.add_argument("--assigned", action="store_true", help="every assigned / in-progress lesson")
    g.add_argument("--available", action="store_true", help="every available lesson")
    g.add_argument("--all", action="store_true", help="every assigned AND available lesson")
    r.add_argument("--download", action="store_true", help="save the lesson videos (default: don't keep them)")
    r.add_argument("--transcribe", action="store_true", help="transcribe the videos (implies --download)")
    r.add_argument("--save-questions", action="store_true",
                   help="save each lesson's questions + correct answers to a text file (pairs with --transcribe)")
    r.add_argument("--out", default="./output", help="download/transcript directory (default ./output)")
    r.add_argument("--model", default="small", help="faster-whisper model (tiny/base/small/medium)")

    args = ap.parse_args()
    auth = (args.landing_url, args.uid, args.coid)

    def matching(lessons, pattern):
        p = pattern.lower()
        return [(i, n) for i, n in lessons if p in n.lower()]

    if args.cmd == "scan":
        assigned, available = with_session(auth, scan)
        if args.pattern:
            assigned, available = matching(assigned, args.pattern), matching(available, args.pattern)
        print(f"Assigned / in progress ({len(assigned)}):")
        for i, n in assigned:
            print(f"  {i:>8}  {n}")
        print(f"\nAvailable ({len(available)}):")
        for i, n in available:
            print(f"  {i:>8}  {n}")
        return

    # run — fetch the lesson list once, both to resolve ids to names in the log
    # and to serve the name-based and group targets.
    keep = args.download or args.transcribe
    assigned, available = with_session(auth, scan)
    by_id = dict(assigned + available)
    if args.lesson:
        targets = [(args.lesson, by_id.get(args.lesson, args.lesson))]
    elif args.name:
        targets = matching(assigned + available, args.name)
        if not targets:
            sys.exit(f"No lesson name contains '{args.name}'. Try `scan {args.name}` to search.")
        if len(targets) > 1:
            print(f"{len(targets)} lessons match '{args.name}':")
            for i, n in targets:
                print(f"  {i}  {n}")
    else:
        targets = []
        if args.assigned or args.all:
            targets += assigned
        if args.available or args.all:
            targets += available

    all_videos, known = [], set()
    for lid, name in targets:
        all_videos += run_lesson(auth, lid, name, args.out, keep, known, args.save_questions)

    seen, uniq = set(), []
    for fn, path in all_videos:
        if path not in seen:
            seen.add(path)
            uniq.append((fn, path))
    all_videos = uniq

    if keep:
        print(f"\nKept {len(all_videos)} video(s) in {args.out}")
        if args.transcribe and all_videos:
            transcribe(all_videos, args.model)
    else:
        print("\nDone (auto-accept only; use --download to keep videos, --transcribe to transcribe).")


if __name__ == "__main__":
    main()
