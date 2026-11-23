"""Public pages only. A blocked source never stops the other sources."""
import html
import json
import re
import time
from itertools import combinations
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import pandas as pd
import requests
from bs4 import BeautifulSoup
import settings as S

FIELDS = ["date", "layer", "event_name", "doors_open", "event_type", "source_url",
          "status", "sold_out", "end_time", "start_time", "data_kind", "notes"]
DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\s*(?:/\s*)?\d{4})\b", re.I)
TIME_RE = r"(\d{1,2}(?:[:.]\d{2})?\s*(?:[ap]\.?m\.?)?)\b"

def today():
    return pd.Timestamp.now(tz=S.TIMEZONE).normalize().tz_localize(None)

def stamp(value):
    if value is None or str(value).strip().lower() in ("", "nan", "nat", "none"):
        return pd.NaT
    try:
        text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(value), flags=re.I)
        text = re.sub(r"([A-Za-z])\s*/\s*(\d{4})", r"\1 \2", text)
        result = pd.to_datetime(text, dayfirst=bool(re.match(r"\d{1,2}/", text)))
        return result.tz_convert(S.TIMEZONE).tz_localize(None) if result.tzinfo else result
    except (ValueError, TypeError, OverflowError):
        return pd.NaT

def clock(value):
    text = str(value or "").strip().lower().replace("a.m.", "am").replace("p.m.", "pm")
    if re.match(r"\d{4}-\d{2}-\d{2}[t ]", text):
        value = stamp(text)
        return value.strftime("%H:%M") if pd.notna(value) else ""
    match = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?", text)
    if not match:
        return ""
    hour, minute, suffix = match.groups()
    hour, minute = int(hour), int(minute or 0)
    if minute > 59 or hour > 23 or (suffix and not 1 <= hour <= 12):
        return ""
    return f"{hour % 12 + (12 if suffix == 'pm' else 0) if suffix else hour:02}:{minute:02}"

class PublicReader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = S.USER_AGENT
        self.rules, self.last_request = {}, {}

    def get(self, url):
        for _ in range(5):
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https"):
                raise ValueError("Not a public HTTP page")
            root = f"{parts.scheme}://{parts.netloc}"
            if root not in self.rules:
                response = self.session.get(root + "/robots.txt", timeout=S.REQUEST_TIMEOUT,
                                            allow_redirects=False)
                self.last_request[root] = time.monotonic()
                rules = RobotFileParser()
                if response.status_code in (404, 410):
                    rules.parse(["User-agent: *", "Disallow:"])
                elif response.status_code == 200 and "<html" not in response.text.lower():
                    rules.parse(response.text.splitlines())
                else:
                    raise ValueError(f"Cannot verify robots.txt (HTTP {response.status_code}); skipped")
                self.rules[root] = rules
            rules = self.rules[root]
            if not rules.can_fetch(S.USER_AGENT, url):
                raise PermissionError("robots.txt disallows this page")
            rate = rules.request_rate(S.USER_AGENT)
            gap = max(S.REQUEST_DELAY, rules.crawl_delay(S.USER_AGENT) or 0,
                      rate.seconds / rate.requests if rate and rate.requests else 0)
            time.sleep(max(0, gap - (time.monotonic() - self.last_request.get(root, 0))))
            response = self.session.get(url, timeout=S.REQUEST_TIMEOUT, allow_redirects=False)
            self.last_request[root] = time.monotonic()
            if response.is_redirect:
                url = urljoin(url, response.headers["Location"])
                continue
            response.raise_for_status()
            return response
        raise ValueError("Too many redirects")

def layer_for(text, default):
    text = str(text).lower()
    for word, layer in [("boxpark", "BOXPARK"), ("troubadour", "Theatre"),
                        ("arena", "Arena"), ("stadium", "Stadium"),
                        ("cineworld", "Cineworld"), ("designer outlet", "LDO")]:
        if word in text:
            return layer
    return default

def classify(text, layer):
    text = text.lower()
    if layer == "Stadium":
        return "stadium sports" if re.search(r"\bv(?:s)?\b|cup|final|rugby|nfl|wrestling", text) else "stadium concert"
    if layer == "Theatre":
        return "theatre"
    if layer == "Cineworld":
        if re.search(r"family|animation|animated|toy story|frozen", text):
            return "family film"
        return "major film" if any(k in text for k in S.MAJOR_FILM_KEYWORDS) else "film release"
    if layer == "LDO":
        return "black friday" if "black friday" in text else "sale"
    if layer == "Arena":
        if re.search(r"family|paw patrol|disney on ice|children", text):
            return "family show"
        return "other" if re.search(r"comedy|boxing|netball", text) else "arena concert"
    return "boxpark event" if layer == "BOXPARK" else "local event"

def row(name, day, layer, url, **extra):
    item = dict.fromkeys(FIELDS, "")
    item.update(date=day, layer=layer, event_name=html.unescape(name), source_url=url,
                event_type=classify(name, layer), status="Scheduled", sold_out=False,
                data_kind="Public listing")
    item.update(extra)
    return item

def dates(text):
    values = [stamp(value) for value in DATE_RE.findall(text)]
    return sorted({d.normalize() for d in values if pd.notna(d)})

def days_between(start, end):
    return pd.date_range(max(start, today()), min(end, today() + pd.Timedelta(days=S.LOOK_AHEAD_DAYS)))

def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)

def parse_page(soup, url, default_layer, mode):
    rows, notes = [], []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        for obj in objects(data):
            kinds = obj.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            movie = mode == "films" and "Movie" in kinds
            if not movie and not any(str(k).endswith("Event") for k in kinds):
                continue
            start = stamp(obj.get("datePublished") if movie else obj.get("startDate"))
            end = stamp(obj.get("endDate"))
            if pd.isna(start) or not obj.get("name"):
                continue
            if pd.notna(end) and end - start > pd.Timedelta(hours=18):
                notes.append("Multi-day run needs individual performance dates")
                continue
            location = json.dumps(obj.get("location", {}))
            layer = layer_for(location, default_layer)
            offers = obj.get("offers", [])
            offers = offers if isinstance(offers, list) else [offers]
            availability = [str(o.get("availability", "")) for o in offers if isinstance(o, dict)]
            sold = bool(availability) and all("soldout" in a.lower() for a in availability)
            item = row(obj["name"], start.normalize(), layer, url,
                       doors_open=clock(obj.get("doorTime")),
                       start_time=clock(obj.get("startDate")),
                       end_time=str(end) if pd.notna(end) else "", sold_out=sold,
                       status=str(obj.get("eventStatus", "Scheduled")),
                       event_type=classify(obj["name"] + " " + str(obj.get("genre", "")), layer))
            if movie:
                # A national opening-weekend signal, not confirmed Wembley showtimes.
                finish = start.normalize() + pd.Timedelta(days=(6-start.weekday()) % 7)
                for day in days_between(start.normalize(), finish):
                    rows.append(dict(item, date=day, notes="National release indicator; verify Wembley screenings"))
            else:
                rows.append(item)

    # Some sites have ordinary cards instead of structured event data.
    if not rows:
        for heading in soup.select("h2,h3,h4"):
            card = heading
            for _ in range(4):
                card = card.parent
                if card is None or len(card.get_text(" ", strip=True)) > 1600:
                    break
                text = card.get_text(" ", strip=True)
                found = dates(text)
                if not found:
                    continue
                if len(card.select("h2,h3,h4")) > 1:
                    break
                if len(found) > 1 and mode != "offers":
                    notes.append("Date range needs manual performance dates")
                    break
                if re.search(r"\b\d{1,2}\s*[-–]\s*\d{1,2}\s+[A-Za-z]", text):
                    notes.append("Abbreviated date range needs review")
                    break
                if mode == "films" and not re.search(r"release|coming soon", text, re.I):
                    break
                name = heading.get_text(" ", strip=True)
                layer = layer_for(text, default_layer)
                item = row(name, found[0], layer, url)
                for label, field in [(r"doors?(?:\s+open)?", "doors_open"),
                                     (r"(?:finish|curfew)", "end_time")]:
                    match = re.search(label + r"\s*[:–-]?\s*" + TIME_RE, text, re.I)
                    if match:
                        item[field] = clock(match.group(1))
                item["status"] = text if re.search("cancel|postpon", text, re.I) else "Scheduled"
                item["sold_out"] = bool(re.search(r"\bsold[\s-]*out\b", text, re.I))
                end = found[-1]
                if mode == "films":
                    end = found[0] + pd.Timedelta(days=(6-found[0].weekday()) % 7)
                    item["notes"] = "National release indicator; verify Wembley screenings"
                for day in days_between(found[0], end):
                    rows.append(dict(item, date=day))
                break
    return rows, sorted(set(notes))

def parse_school(soup, url):
    rows, terms = [], []
    for block in soup.select("main p,main li,main tr,article p"):
        text = block.get_text(" ", strip=True)
        found = dates(text)
        if len(found) != 2:
            continue
        start, end = found
        if re.search(r"half[ -]?term|(?:christmas|easter|summer)\s+holidays?|school holidays", text, re.I):
            label = text.split(":")[0].strip()
            for day in days_between(start, end):
                rows.append(row("Brent " + label, day, "Calendar", url,
                                event_type="school holiday", notes="Community schools; other schools may differ"))
        elif 40 <= (end-start).days <= 130 and re.search(r"\bto\b|[–—]", text):
            terms.append((start, end))
    # Gaps between consecutive published terms cover Christmas, Easter and summer.
    terms = sorted(set(terms))
    for (_, end), (start, _) in zip(terms, terms[1:]):
        if 1 < (start-end).days < 80:
            for day in days_between(end + pd.Timedelta(days=1), start - pd.Timedelta(days=1)):
                rows.append(row("Brent school holiday", day, "Calendar", url,
                                event_type="school holiday", notes="Between published community-school terms"))
    return rows

def fetch_public():
    reader, rows, reports = PublicReader(), [], []
    for name, (url, layer, mode) in S.SOURCES.items():
        try:
            response = reader.get(url)
            notes = []
            if mode == "bank":
                parsed = [row(e["title"], e["date"], "Calendar", url, event_type="bank holiday")
                          for e in response.json()["england-and-wales"]["events"]]
            else:
                soup = BeautifulSoup(response.text, "html.parser")
                if mode == "school":
                    parsed = parse_school(soup, response.url)
                else:
                    parsed, notes = parse_page(soup, response.url, layer, mode)
            parsed = [r for r in parsed if pd.notna(stamp(r["date"])) and
                      today() <= stamp(r["date"]).normalize() <= today()+pd.Timedelta(days=S.LOOK_AHEAD_DAYS)]
            rows.extend(parsed)
            message = (f"{len(parsed)} upcoming rows. Listing-page coverage only."
                       if parsed else "No readable upcoming dates; use manual_events.csv. Page may need JavaScript.")
            reports.append(dict(source=name, result=message + " " + "; ".join(notes), source_url=url))
        except Exception as exc:
            reports.append(dict(source=name, result=f"FAILED: {type(exc).__name__}: {exc}. Using manual CSV where supplied.", source_url=url))
    return rows, reports, pd.Timestamp.now(tz=S.TIMEZONE)

def read_manual(path="manual_events.csv"):
    try:
        table = pd.read_csv(Path(__file__).parent / path, dtype=str, keep_default_na=False)
        required = set(FIELDS[:6])
        if not required.issubset(table.columns):
            raise ValueError("Missing columns: " + ", ".join(sorted(required-set(table.columns))))
        return table.to_dict("records"), ""
    except Exception as exc:
        return [], f"Manual CSV could not be read: {exc}"

def sample_rows():
    examples = [(1,"Arena","SAMPLE — concert","18:00","arena concert"),
                (3,"Stadium","SAMPLE — football","15:00","stadium sports"),
                (3,"Theatre","SAMPLE — musical","18:30","theatre"),
                (5,"Cineworld","SAMPLE — family film","","family film"),
                (8,"LDO","SAMPLE — sale","","sale")]
    return [row(name, today()+pd.Timedelta(days=offset), layer, "", doors_open=doors,
                event_type=kind, data_kind="SAMPLE", notes="Fictional demonstration only")
            for offset,layer,name,doors,kind in examples]

def clean(raw):
    cleaned, warnings = [], []
    for source in raw:
        item = {key: source.get(key, "") for key in FIELDS}
        day = stamp(item["date"])
        if pd.isna(day) or item["layer"] not in S.LAYERS or not str(item["event_name"]).strip():
            warnings.append("Skipped a row with an invalid date, layer or event name")
            continue
        item["date"] = day.normalize()
        if not today() <= item["date"] <= today()+pd.Timedelta(days=S.LOOK_AHEAD_DAYS):
            continue
        for field in ["doors_open", "start_time"]:
            value = clock(item[field])
            if item[field] and not value:
                warnings.append(f"Unrecognised {field} for {item['event_name']}; left blank")
            item[field] = value
        evidence = (str(item["status"]) + " " + str(item["event_name"])).lower()
        item["status"] = "Cancelled" if "cancel" in evidence else "Postponed" if "postpon" in evidence else "Scheduled"
        item["sold_out"] = str(item["sold_out"]).lower() in ("true", "yes", "1") or bool(re.search(r"sold[ -]?out", evidence))
        item["event_type"] = item["event_type"] or classify(item["event_name"], item["layer"])
        item["data_kind"] = item["data_kind"] or "Manual"
        item["_name"] = re.sub(r"\W+", "", re.sub(
            r"^(cancelled|canceled|postponed)\s*[-:–]?\s*|\s*[-–]\s*away supporters.*$",
            "", item["event_name"].lower()))
        if item["event_type"] == "school holiday":
            item["_name"] = "schoolholiday"
        cleaned.append(item)
    if not cleaned:
        return pd.DataFrame(columns=FIELDS), warnings
    table = pd.DataFrame(cleaned)
    output = []
    # Prefer manual corrections; merge sources without counting a listing twice.
    for _, group in table.groupby(["date", "layer", "_name"], sort=False):
        group = group.copy()
        # Start times and doors times describe different things. Do not mistake
        # an earlier manual doors time for an extra performance.
        has_starts = group.start_time.ne("").any()
        group["_slot"] = group.start_time if has_starts else group.doors_open
        known = group.loc[group["_slot"].ne(""), "_slot"].unique()
        if len(known) == 1:
            group.loc[group["_slot"].eq(""), "_slot"] = known[0]
        elif len(known) > 1 and group["_slot"].eq("").any():
            warnings.append("Dropped a duplicate listing with no performance time: " + group.iloc[0].event_name)
            group = group[group["_slot"].ne("")]
        for _, performance in group.groupby("_slot", dropna=False):
            performance = performance.sort_values("data_kind", key=lambda c: c.eq("Manual"))
            item = performance.iloc[-1].to_dict()
            for field in ["doors_open", "start_time", "end_time"]:
                values = performance.loc[performance[field].ne(""), field]
                item[field] = values.iloc[-1] if len(values) else ""
            item["source_url"] = " | ".join(dict.fromkeys(performance.source_url))
            # A manual status can correct an outdated cancellation notice.
            if not performance.data_kind.eq("Manual").any():
                item["status"] = next((s for s in ["Cancelled","Postponed"] if s in performance.status.values), "Scheduled")
            item["sold_out"] = bool(performance.iloc[-1].sold_out if performance.data_kind.eq("Manual").any()
                                    else performance.sold_out.any())
            output.append({k: item[k] for k in FIELDS})
    return pd.DataFrame(output).sort_values(["date", "layer", "event_name"]), sorted(set(warnings))

def action(rating):
    value = S.ACTIONS[rating]
    return f"Extra staff: {value['staff']} · prep +{value['prep']}% · stock +{value['stock']}%"

def trade_window(item):
    if item.status != "Scheduled" or not item.doors_open:
        return pd.NaT, pd.NaT, pd.NaT, pd.NaT, "Doors unknown — confirm trade windows" if item.status == "Scheduled" else "Excluded"
    doors = item.date + pd.Timedelta(hours=int(item.doors_open[:2]), minutes=int(item.doors_open[3:]))
    end = stamp(item.end_time) if re.match(r"\d{4}-", str(item.end_time)) else pd.NaT
    end_clock = clock(item.end_time)
    if pd.isna(end) and end_clock:
        end = item.date + pd.Timedelta(hours=int(end_clock[:2]), minutes=int(end_clock[3:]))
        if end < doors:
            end += pd.Timedelta(days=1)
    assumed = pd.isna(end) or end <= doors
    if assumed:
        end = doors + pd.Timedelta(hours=S.ASSUMED_HOURS_DOORS_TO_FINISH)
    before, after = doors-pd.Timedelta(hours=S.HOURS_BEFORE_DOORS), end+pd.Timedelta(hours=S.HOURS_AFTER_FINISH)
    fmt = lambda value: value.strftime("%d %b %H:%M")
    label = f"Before {fmt(before)}–{fmt(doors)}; after {fmt(end)}–{fmt(after)}"
    return before, doors, end, after, label + (" (finish assumed)" if assumed else " (listed finish)")

def analyse(events):
    events = events.copy()
    if events.empty:
        return events, pd.DataFrame()
    events["active"] = events.status.eq("Scheduled")
    events["estimated_attendance"] = events.apply(
        lambda r: round(S.CAPACITY[r.layer] * (1 if r.sold_out else S.FILL_RATE.get(r.event_type, S.FILL_RATE["other"])))
        if r.active and r.layer in S.CAPACITY else pd.NA, axis=1).astype("Int64")
    events["attendance_basis"] = events.apply(lambda r: "Excluded" if not r.active else
        "Estimate: capacity × " + f"{(1 if r.sold_out else S.FILL_RATE.get(r.event_type, S.FILL_RATE['other'])):.0%}"
        if r.layer in S.CAPACITY else "Rule-based impact; no attendance estimate", axis=1)
    windows = events.apply(trade_window, axis=1).tolist()
    events[["_window_start", "_doors", "_finish", "_window_end", "peak_trade_window"]] = pd.DataFrame(windows, index=events.index)
    days = []
    for day, group in events.groupby("date"):
        active = group[group.active]
        total = active.estimated_attendance.sum(min_count=1)
        score = sum(total >= n for n in S.ATTENDANCE_THRESHOLDS) if pd.notna(total) else 0
        rules = [S.RATINGS.index(S.RULE_IMPACT.get(t,"Low")) for t in active.event_type]
        score = max([score] + rules)
        # Many shop offers form one LDO driver, not many separate crowd events.
        drivers = {f"{r.layer}:{r.event_name}:{r.start_time or r.doors_open}"
                   if r.layer in S.CAPACITY else r.layer + ":" + (r.event_type if r.layer == "Calendar" else "")
                   for r in active.itertuples() if r.event_type != "school holiday"}
        if len(active):
            score += S.WEEKEND_BUMP * (day.weekday() >= 5)
            score += S.MULTIPLE_THINGS_BUMP * (len(drivers) >= 2)
            score += S.SCHOOL_HOLIDAY_BUMP * active.event_type.eq("school holiday").any()
            score += S.STADIUM_BUMP * active.layer.eq("Stadium").any()
        rating = S.RATINGS[min(3, int(score))] if len(active) else "Excluded"
        timed = active.dropna(subset=["_window_start", "_window_end"])
        intervals = lambda r: [(r._window_start,r._doors),(r._finish,r._window_end)]
        overlaps = any(a1 < b2 and b1 < a2
                       for a,b in combinations([r for _,r in timed.iterrows()],2)
                       for a1,a2 in intervals(a) for b1,b2 in intervals(b))
        event_overlap = any(a._doors < b._finish and b._doors < a._finish
                            for a,b in combinations([r for _,r in timed.iterrows()],2))
        warning = ("Estimated trade windows overlap; confirm actual timings" if overlaps else
                   "Estimated event times overlap; confirm actual timings" if event_overlap else
                   "Same-day activities; some timings unknown" if len(drivers)>=2 and len(timed)<len(active) else "")
        days.append(dict(date=day, impact=rating, estimated_attendance=total,
                         events=len(active), unknown_attendance=int(active.estimated_attendance.isna().sum()),
                         warning=warning, actions=action(rating) if len(active) else "No uplift — events inactive"))
    daily = pd.DataFrame(days)
    events["impact_rating"] = events.date.map(daily.set_index("date").impact)
    events.loc[~events.active, "impact_rating"] = "Excluded"
    events["recommended_actions"] = events.date.map(daily.set_index("date").actions)
    events.loc[~events.active, "recommended_actions"] = "No uplift — event inactive"
    return events, daily

def csv_bytes(table):
    table = table[[c for c in table.columns if not c.startswith("_")]].copy()
    if "date" in table:
        table["date"] = pd.to_datetime(table.date).dt.strftime("%Y-%m-%d")
    for column in table.select_dtypes(include=["object", "string"]).columns:
        table[column] = table[column].map(lambda v: "'"+v if isinstance(v,str) and v.lstrip().startswith(("=","+","-","@")) else v)
    return table.to_csv(index=False).encode("utf-8-sig")
