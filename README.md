# sentix-lms

A small command-line tool to automate a **Sentix LMS** (SambaSafety) training
account: log in, list your lessons, and walk any lesson from start to finish —
playing through the video pages and answering the knowledge-check questions —
while **downloading every lesson video** and, optionally, **transcribing** the
narration to text.

It exists so you can **archive and review training on an account you own**
without clicking through each page by hand. Every run opens its own fresh,
isolated session, so concurrent runs never corrupt each other's progress.

> **Use responsibly.** Only run this against an account you own and are
> authorised to use. You are responsible for how you use it and for complying
> with your organisation's and the platform's terms.

---

## How it works

A Sentix lesson is a sequence of pages. Each page is either a **video** or a
**question**. The tool reproduces exactly what the web player does:

- **Video page** — downloads the page's MP4, then records the required watch
  time and advances.
- **Question page** — submits answer options until the server accepts a correct
  one, then advances.

It reads the page state (`ThisPage`, `Type`, `Filename`, `TotalPages`) straight
from the player and loops until the last page. The one quirk it handles for you:
the "next" navigation can return the site's error page even when it actually
succeeded, so the tool always re-reads the player to get the true page.

---

## Requirements

- **Python 3.8+**
- **`curl`** on your `PATH` (preinstalled on macOS and most Linux).
- For the optional `--transcribe` step only:

  ```bash
  pip install -r requirements.txt
  ```

  This pulls in [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper),
  which bundles its own audio decoder (PyAV) — **no separate ffmpeg install
  needed.** The speech model downloads automatically on first use.

Downloading videos and walking lessons needs **no** extra packages — just Python
and `curl`.

---

## Authentication

Provide credentials one of two ways (flags or environment variables). **Nothing
is stored** — cookies live in a throwaway file that is deleted when the run ends.

| What | Flag | Environment variable |
| --- | --- | --- |
| One-click auto-login link | `--landing-url URL` | `SENTIX_LANDING_URL` |
| User ID | `--uid UID` | `SENTIX_UID` |
| Company ID | `--coid COID` | `SENTIX_COID` |

Use **either** the landing link **or** the User ID + Company ID pair.

```bash
# with the auto-login link
export SENTIX_LANDING_URL='https://www.sentixlms.com/landing.lasso?destination=...'

# or with User ID + Company ID
export SENTIX_UID='000000'
export SENTIX_COID='000000'
```

---

## Usage

### List your lessons

```bash
python3 sentix_lms.py scan
```

```
Assigned / in progress (2):
    123456  Some Assigned Lesson
    123457  Another Assigned Lesson

Available (58):
    123460  Some Available Lesson
    ...
```

### Walk a lesson (download videos)

```bash
# a single lesson by id
python3 sentix_lms.py run --lesson 123456

# every assigned / in-progress lesson
python3 sentix_lms.py run --assigned

# everything (assigned + available)
python3 sentix_lms.py run --all
```

Videos are saved to `./output/` (override with `--out DIR`) as
`<lessonId>_<original-filename>.mp4`. Re-running skips files already downloaded.

### Also transcribe the narration

```bash
python3 sentix_lms.py run --lesson 123456 --transcribe
python3 sentix_lms.py run --assigned --transcribe --model small
```

Each video gets a `<video>.mp4.txt` with time-stamped narration beside it.
`--model` accepts any faster-whisper size (`tiny`, `base`, `small`, `medium`);
`small` is a good speed/accuracy balance on CPU.

---

## Command reference

```
scan                       list assigned and available lessons
run  --lesson ID           walk one lesson
     --assigned            walk every assigned / in-progress lesson
     --all                 walk every assigned AND available lesson
     --out DIR             output directory (default ./output)
     --transcribe          transcribe downloaded videos to text
     --model NAME          faster-whisper model (default: small)

Auth (any command): --landing-url URL | --uid UID --coid COID
```

---

## Output & privacy

- Downloaded videos and transcripts go under `output/` and are **git-ignored** —
  training content stays local and is never committed.
- Credentials are never written to disk; the per-run cookie jar is deleted on
  exit.

## License

MIT
