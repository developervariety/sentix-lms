# sentix-lms

A small command-line tool to automate a **Sentix LMS** (SambaSafety) training
account: log in, list your lessons, and **complete** any lesson — playing
through the video pages and answering the knowledge-check questions, including
finalizing the last page so the lesson is marked complete (which unlocks the
next available lessons). Downloading the lesson videos and transcribing the
narration are **optional** flags.

It exists so you can **complete, archive, and review training on an account you
own** without clicking through each page by hand. Every run opens its own fresh,
isolated session, so concurrent runs never corrupt each other's progress.

> **Use responsibly.** Only run this against an account you own and are
> authorised to use. You are responsible for how you use it and for complying
> with your organisation's and the platform's terms.

---

## How it works

A Sentix lesson is a sequence of pages. Each page is either a **video** or a
**question**. The tool reproduces what the web player does:

- **Video page** — records the required watch time and advances. (The MP4 is
  fetched to read its length; it is kept only with `--download`.)
- **Question page** — finds the correct option and submits it, then advances.
- **Last page** — submits the final page so the server marks the lesson
  complete, which is what unlocks the next available lessons.

It reads the page state (`ThisPage`, `Type`, `Filename`, `TotalPages`) straight
from the player and loops until the lesson is finished.

A couple of platform quirks it handles for you:

- The "next" navigation can return the site's error page even when it actually
  succeeded, so the tool always re-reads the player to get the true page.
- Submitting a *wrong* answer locks that question for the rest of the session,
  so the tool discovers the correct option in one session and applies it cleanly
  from a fresh one. That is also why it uses a fresh session per run/retry.

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

Completing lessons and downloading videos needs **no** extra packages — just
Python and `curl`.

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

### Complete lessons

By default `run` just **completes** the lesson(s) — no files are saved.

```bash
# a single lesson by id
python3 sentix_lms.py run --lesson 123456

# every assigned / in-progress lesson
python3 sentix_lms.py run --assigned

# every available lesson
python3 sentix_lms.py run --available

# everything (assigned + available)
python3 sentix_lms.py run --all
```

### Also keep the videos (`--download`)

```bash
python3 sentix_lms.py run --lesson 123456 --download
```

Videos are saved to `./output/` (override with `--out DIR`) as
`<lessonId>_<original-filename>.mp4`. Re-running skips files already downloaded.

### Also transcribe the narration (`--transcribe`)

```bash
python3 sentix_lms.py run --lesson 123456 --transcribe
python3 sentix_lms.py run --assigned --transcribe --model small
```

`--transcribe` implies `--download`. Each video gets a `<video>.mp4.txt` with
time-stamped narration beside it. `--model` accepts any faster-whisper size
(`tiny`, `base`, `small`, `medium`); `small` is a good speed/accuracy balance on
CPU.

> Videos are collected as the lesson is walked, so `--download`/`--transcribe`
> capture everything on a lesson that still has pages left to complete. A lesson
> that is already finished resumes at its last page and has nothing new to
> collect.

---

## Command reference

```
scan                       list assigned and available lessons
run  --lesson ID           complete one lesson
     --assigned            complete every assigned / in-progress lesson
     --available           complete every available lesson
     --all                 complete every assigned AND available lesson
     --download            also keep the lesson videos
     --transcribe          also transcribe them (implies --download)
     --out DIR             output directory (default ./output)
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
