"""Official transcripts of past events, split by speaker.

Source used: Federal Reserve FOMC press-conference transcripts (federalreserve.gov, the official
"FINAL" PDF linked from each press-conference page). Only the Chair's words count for Kalshi's
Fed mention markets ("If the Chair of the Federal Reserve says ... at his post-FOMC meeting
introductory remarks and Q+A"), so reporters' questions and the moderator are dropped.

Dated negative findings (2026-10-22): whitehouse.gov does not post press-briefing transcripts as a
regular series; company earnings-call transcripts are not on company IR sites as a rule and are not
in SEC filings; sports broadcasts have no public transcript. Those series are priced from Kalshi's
own settled results instead (see baserate.py).
"""
import io
import re
from datetime import datetime, timezone

from . import http

FED = "https://www.federalreserve.gov"
_SPEAKER = re.compile(r"^([A-Z][A-Z.'’ -]{2,40})\.\s+", re.M)
_HEADER = re.compile(r"^.*Press Conference\s+FINAL\s*$|^\s*Page \d+ of \d+\s*$", re.M | re.I)


def fed_dates(since_year=2022):
    """Dates (YYYYMMDD) of every FOMC press conference listed on the Fed's calendar page."""
    html = http.get(FED + "/monetarypolicy/fomccalendars.htm", raw=True) or b""
    ds = sorted(set(re.findall(rb"fomcpresconf(\d{8})\.htm", html, re.I)))
    return [d.decode() for d in ds if int(d[:4]) >= since_year]


def pdf_text(data):
    import pypdf
    return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(data)).pages)


def split_speakers(text):
    """[(speaker, words)] in order. Page headers are removed first."""
    text = _HEADER.sub("", text)
    parts = _SPEAKER.split(text)
    out = []
    for i in range(1, len(parts) - 1, 2):
        out.append((parts[i].strip(), parts[i + 1]))
    return out


def chair_text(text):
    """Only what the Chair said (speaker label starting CHAIR, e.g. 'CHAIR POWELL', 'CHAIRMAN WARSH')."""
    return "\n".join(w for s, w in split_speakers(text) if s.startswith("CHAIR"))


def fed_transcript(date):
    """{'date', 'held_utc', 'chair', 'text'} for one press conference, or None if not posted.

    held_utc is 2:30 PM ET on the day (the press conference start; the transcript is posted later),
    used only to order events: a base rate for a target event may use transcripts with
    held_utc strictly before the target's start."""
    data = http.get(f"{FED}/mediacenter/files/FOMCpresconf{date}.pdf", raw=True, cache=True)
    if not data:
        return None
    full = pdf_text(data)
    chair = [s for s, _ in split_speakers(full) if s.startswith("CHAIR")]
    held = datetime(int(date[:4]), int(date[4:6]), int(date[6:]), 18, 30, tzinfo=timezone.utc)
    return {"date": date, "held_utc": held, "chair": chair[0] if chair else "", "text": chair_text(full),
            "source": f"{FED}/mediacenter/files/FOMCpresconf{date}.pdf"}


def fed_transcripts(since_year=2022):
    return [t for t in (fed_transcript(d) for d in fed_dates(since_year)) if t]
