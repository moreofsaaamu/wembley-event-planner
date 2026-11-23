"""Run on Streamlit Community Cloud with app.py as the main file."""
import calendar
import html
import json
from urllib.parse import urlsplit

import pandas as pd
import plotly.express as px
import streamlit as st
import settings as S
from scraper import (FIELDS, action, analyse, clean, clock, csv_bytes, fetch_public,
                     read_manual, sample_rows, today)

st.set_page_config(page_title="Wembley Event Planner", page_icon="🌶️", layout="centered")
st.markdown("""<style>
.stApp {background:#F5EBDD;color:#181818}
.block-container {max-width:1000px;padding-top:2rem;padding-bottom:2rem}
h1,h2,h3 {color:#181818} a {color:#A71920}
.day-card {background:#fffaf3;border:1px solid #dccdbb;border-left:6px solid #BB2026;
 border-radius:12px;padding:18px;margin:16px 0;color:#181818;font-size:16px}
.badge {display:inline-block;border-radius:20px;padding:4px 12px;font-weight:700}
.event {border-top:1px solid #e6dacb;padding:12px 0}
.small {font-size:14px;color:#51463a}.actions {font-weight:700;margin:12px 0}
.month {display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px}
.date-box {padding:8px 2px;border-radius:6px;min-height:70px;text-align:center;font-size:14px}
@media(max-width:600px){.block-container{padding:1rem}.day-card{padding:12px}}
</style>""", unsafe_allow_html=True)

@st.cache_data(ttl=S.CACHE_HOURS*3600, show_spinner=False)
def public_data(source_settings):
    return fetch_public()

def escape(value):
    return html.escape(str(value))

def badge(rating):
    colour = S.COLOURS.get(rating, "#ddd")
    ink = "white" if rating in ["High", "Very High"] else "#181818"
    return f'<span class="badge" style="background:{colour};color:{ink}">{escape(rating)}</span>'

def attendance(value):
    return "No capacity estimate" if pd.isna(value) else f"{int(value):,} attendance estimate"

def show_day(summary, events):
    content = [f'<div class="day-card"><h3>{summary.date:%A %d %B}</h3>',
               badge(summary.impact), f'<p>{attendance(summary.estimated_attendance)}</p>',
               f'<p class="actions">{escape(summary.actions)}</p>']
    if summary.warning:
        content.append(f'<p><strong>Timing warning:</strong> {escape(summary.warning)}</p>')
    events = events.sort_values("estimated_attendance", ascending=False, na_position="last")
    for index, event in enumerate(events.itertuples()):
        if index == 4:
            content.append(f'<details><summary>Show {len(events)-4} more items</summary>')
        content.append(f'<div class="event"><strong>{escape(event.event_name)}</strong>'
                       f'<div>{escape(event.layer)} · {escape(event.status)} · '
                       f'Doors {escape(event.doors_open or "unknown")}</div>'
                       f'<div>{attendance(event.estimated_attendance) if event.active else "Excluded from ratings"}</div>'
                       f'<div class="small">{escape(event.peak_trade_window)}</div>')
        if event.notes:
            content.append(f'<div class="small">{escape(event.notes)}</div>')
        for url in event.source_url.split(" | "):
            if urlsplit(url).scheme in ("http", "https"):
                content.append(f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Public source</a> ')
        content.append('</div>')
    if len(events) > 4:
        content.append('</details>')
    st.markdown("".join(content) + "</div>", unsafe_allow_html=True)

st.session_state.setdefault("manual_additions", [])

@st.fragment(run_every="60s")
def dashboard():
    st.title("Wembley Event Planner")
    st.caption("Nando’s Wembley · plan staffing, prep and stock")
    with st.spinner("Checking public listings. First load can take a few minutes…"):
        public, reports, updated = public_data(json.dumps(S.SOURCES, sort_keys=True))
    age = (pd.Timestamp.now(tz=S.TIMEZONE)-updated).total_seconds()/60
    st.caption(f"Last updated: {updated:%d %b %Y, %H:%M %Z} · automatic cache: {S.CACHE_HOURS} hours")
    if st.button("Refresh data", disabled=age<S.REFRESH_COOLDOWN_MINUTES,
                 help=f"Available {S.REFRESH_COOLDOWN_MINUTES} minutes after the last check, to protect source websites."):
        public_data.clear()
        st.rerun()
    stored, manual_error = read_manual()
    manual = [dict(r, data_kind="Manual") for r in stored + st.session_state.manual_additions]
    events, warnings = clean(public + manual)
    if events.empty:
        events, _ = clean(sample_rows())
        st.warning("SAMPLE DATA — fictional events. No usable upcoming public or manual rows were available.")
    st.caption("Venue attendance estimates are not restaurant customers. Classification, fill rates and actions are planning assumptions.")
    with st.expander("Source health and coverage"):
        st.dataframe(pd.DataFrame(reports), hide_index=True, width="stretch")
        st.write(f"Manual CSV: {len(stored)} rows. Session additions: {len(st.session_state.manual_additions)}.")
        st.caption("Listing pages only: JavaScript calendars, pagination and unconfirmed performance dates may be missing. Cineworld release dates do not confirm Wembley screenings. Brent dates cover community schools.")
        if manual_error:
            st.warning(manual_error)
        for warning in warnings:
            st.warning(warning)
    period = st.radio("Date range", ["Next 7 days", "Next 14 days", "Next month"], horizontal=True)
    layers = st.pills("Show layers", S.LAYERS, selection_mode="multi", default=S.LAYERS)
    end = today()+pd.DateOffset(months=1) if period=="Next month" else today()+pd.Timedelta(days=7 if period=="Next 7 days" else 14)
    st.caption("Next month means one calendar month from today. Ratings recalculate for the visible layers.")
    visible = events[events.layer.isin(layers) & events.date.ge(today()) & events.date.lt(end)]
    view, daily = analyse(visible)
    # Export all layers and all dates in the look-ahead period, regardless of filters.
    full, _ = analyse(events)
    st.download_button("Download all events.csv", csv_bytes(full), "events.csv", "text/csv")
    if view.empty:
        st.info("No listings match these filters. Try another range or add a public event below.")
        return
    upcoming = daily[daily.impact.isin(["High", "Very High"])]
    if not upcoming.empty:
        first = upcoming.iloc[0]
        st.error(f"Next {first.impact} day: {first.date:%a %d %b}. {first.actions}")
    for item in daily[daily.warning.ne("")].itertuples():
        st.warning(f"{item.date:%d %b}: {item.warning}")
    cards, cal, table = st.tabs(["Day cards", "Calendar", "Table"])
    with cards:
        for day in daily.itertuples():
            show_day(day, view[view.date.eq(day.date)])
    with cal:
        months = pd.period_range(today(), end-pd.Timedelta(days=1), freq="M")
        month = pd.Period(st.selectbox("Month", [str(m) for m in months]), freq="M")
        cells = ['<div class="month">'] + [f'<strong style="text-align:center">{d}</strong>' for d in ["M","T","W","T","F","S","S"]]
        summaries = daily.set_index("date")
        for week in calendar.monthcalendar(month.year, month.month):
            for number in week:
                date = pd.Timestamp(month.year,month.month,number) if number else None
                summary = summaries.loc[date] if date in summaries.index else None
                rating = summary.impact if summary is not None else ""
                ink = "white" if rating in ["High","Very High"] else "#181818"
                label = f'{int(summary.events)} items' if summary is not None else ""
                cells.append(f'<div class="date-box" style="background:{S.COLOURS.get(rating,"#eee5d8")};color:{ink}">'
                             f'<strong>{number or ""}</strong><br>{label}<br>{escape(rating)}</div>')
        st.markdown("".join(cells)+"</div>", unsafe_allow_html=True)
        st.caption("Blank days have no matching listings, not a confirmed quiet forecast.")
        picked = st.selectbox("Show a day's details", list(daily.date), format_func=lambda d:d.strftime("%a %d %b"))
        show_day(daily[daily.date.eq(picked)].iloc[0], view[view.date.eq(picked)])
    with table:
        columns = ["date","layer","event_name","doors_open","estimated_attendance",
                   "impact_rating","recommended_actions","status","peak_trade_window","source_url"]
        display_table = view[columns].copy()
        display_table["date"] = display_table.date.dt.strftime("%Y-%m-%d")
        st.dataframe(display_table.rename(columns={"estimated_attendance":"Attendance (estimate)"}),
                     hide_index=True, width="stretch")
    plotted = daily.dropna(subset=["estimated_attendance"]).copy()
    if not plotted.empty:
        plotted["estimated_attendance"] = plotted.estimated_attendance.astype(float)
        fig = px.bar(plotted, x="date", y="estimated_attendance", color="impact",
                     color_discrete_map=S.COLOURS, category_orders={"impact":S.RATINGS},
                     hover_data=["unknown_attendance"], labels={"date":"Date", "impact":"Day impact",
                     "estimated_attendance":"Attendance estimate", "unknown_attendance":"Unestimated items"})
        fig.update_layout(paper_bgcolor="#F5EBDD",plot_bgcolor="#F5EBDD",font_color="#181818")
        fig.update_traces(marker_line_color="#181818",marker_line_width=1)
        st.plotly_chart(fig, width="stretch")
    st.caption("Daily sums are event attendances, not unique visitors. Cinema, offers and holidays have no capacity estimate and are excluded from bar totals.")

dashboard_area = st.container()

with st.expander("Add a public event"):
    st.info("This form saves to your current browser session only. Download the manual CSV below and replace manual_events.csv in GitHub to share it permanently.")
    with st.form("add_event", clear_on_submit=True):
        date = st.date_input("Event date", value=today().date())
        layer = st.selectbox("Layer", S.LAYERS)
        name = st.text_input("Event name (add ‘matinee’ or ‘evening’ for separate performances)")
        doors = st.text_input("Doors time (optional)", placeholder="6pm or 18:00")
        kind = st.selectbox("Event type", list(dict.fromkeys(list(S.FILL_RATE)+list(S.RULE_IMPACT))))
        url = st.text_input("Public source URL", placeholder="https://…")
        status = st.selectbox("Status", ["Scheduled","Cancelled","Postponed"])
        sold = st.checkbox("Official page says the whole event is sold out")
        submitted = st.form_submit_button("Add to this session")
    if submitted:
        if not name.strip() or urlsplit(url).scheme not in ("http","https") or not urlsplit(url).netloc:
            st.error("Enter an event name and a valid public source URL.")
        elif doors.strip() and not clock(doors):
            st.error("Use a time such as 6pm, 18:00 or 6.30pm; otherwise leave it blank.")
        elif not today().date() <= date <= (today()+pd.Timedelta(days=S.LOOK_AHEAD_DAYS)).date():
            st.error(f"Choose a date in the next {S.LOOK_AHEAD_DAYS} days.")
        else:
            st.session_state.manual_additions.append(dict(date=str(date), layer=layer,
                event_name=name.strip(), doors_open=clock(doors), event_type=kind,
                source_url=url.strip(), status=status, sold_out=sold, end_time=""))
            st.success("Added for this session. Download the manual CSV to save it in GitHub.")
    saved, _ = read_manual()
    edits = pd.DataFrame(saved + st.session_state.manual_additions,
                        columns=FIELDS[:9]).fillna("")
    st.download_button("Download manual_events.csv for GitHub", csv_bytes(edits),
                       "manual_events.csv", "text/csv")
    if st.button("Clear this session’s additions"):
        st.session_state.manual_additions = []
        st.success("Session additions cleared.")

with dashboard_area:
    dashboard()

st.divider()
st.caption("Estimates only. Internal use. No customer data collected.")
