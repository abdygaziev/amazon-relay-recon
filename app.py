import streamlit as st
import pandas as pd
import io
from datetime import datetime, timedelta

st.set_page_config(page_title="RelayRecon", page_icon="🚛", layout="wide")

# Enhanced CSS for better UI
st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 0rem; }
    
    /* Metric cards */
    .stMetric {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 15px;
        border-radius: 10px;
        color: white;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .stMetric > div { color: white; }
    .stMetric label { color: rgba(255,255,255,0.9) !important; }
    
    /* Headers */
    h1 { color: #1E3A8A; margin-bottom: 0.5rem; }
    h2 { color: #374151; margin-top: 1rem; margin-bottom: 0.5rem; }
    h3 { color: #4B5563; margin-top: 0.75rem; }
    
    /* Expanders */
    div[data-testid="stExpander"] {
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        margin-bottom: 0.5rem;
    }
    div[data-testid="stExpander"] > div:first-child {
        background-color: #F9FAFB;
        min-height: 2.5rem;
        font-weight: 600;
    }
    
    /* DataFrames */
    .stDataFrame {
        font-size: 13px;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
    }
    
    /* Buttons */
    .stDownloadButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 10px 20px;
        font-weight: 600;
    }
    
    /* Sidebar */
    .css-1d391kg { background-color: #F3F4F6; }
    
    /* Success/Error/Warning boxes */
    .stSuccess { border-left: 4px solid #10B981; }
    .stError { border-left: 4px solid #EF4444; }
    .stWarning { border-left: 4px solid #F59E0B; }
    .stInfo { border-left: 4px solid #3B82F6; }
    
    /* Compact tables */
    .dataframe {
        font-size: 12px !important;
    }
    .dataframe th {
        background-color: #374151;
        color: white;
        font-weight: 600;
    }
    .dataframe tr:nth-child(even) {
        background-color: #F9FAFB;
    }
</style>
""", unsafe_allow_html=True)

st.title("🚛 RelayRecon")
st.caption("Amazon Relay trip reconciliation — finds missing payments")

# ── Helpers ──────────────────────────────────────────────────────────────
def safe_float(s):
    try: return float(str(s).replace("$","").replace(",","").strip())
    except: return 0.0

def parse_date(date_str, time_str=""):
    d = str(date_str or "").strip()
    t = str(time_str or "").strip()
    if not d or d.upper()=="NAN": return None
    for fmt in ["%m/%d/%Y %H:%M","%m/%d/%Y","%Y-%m-%d %H:%M:%S","%Y-%m-%d"]:
        try: return pd.to_datetime(f"{d} {t}".strip(), format=fmt)
        except: pass
    return pd.to_datetime(f"{d} {t}".strip(), errors="coerce")

def normalize_driver(driver_str):
    """Normalize driver names: split by semicolon, dedupe, sort for consistent team driver order"""
    if not driver_str: return ""
    names = [n.strip() for n in str(driver_str).split(";") if n.strip()]
    unique_names = list(dict.fromkeys(names))
    if len(unique_names) == 1:
        return unique_names[0]
    unique_names.sort()
    return ";".join(unique_names)

# ── Sidebar ──────────────────────────────────────────────────────────────
st.sidebar.header("Upload Files")
trips_file = st.sidebar.file_uploader("Amazon Relay History", type="csv", key="trips")
payment_files = st.sidebar.file_uploader("Payment Details (Excel or CSV)", 
                                          type=["xlsx", "xls", "csv"],
                                          accept_multiple_files=True, key="payment")

if trips_file and payment_files:
    trips_df = pd.read_csv(trips_file)
    
    # Read payment files - handle both CSV and Excel
    payment_dfs = []
    for f in payment_files:
        if f.name.endswith(('.xlsx', '.xls')):
            xl = pd.ExcelFile(f)
            sheet_name = "Payment Details" if "Payment Details" in xl.sheet_names else None
            if not sheet_name:
                detail_sheets = [s for s in xl.sheet_names if "detail" in s.lower()]
                sheet_name = detail_sheets[0] if detail_sheets else xl.sheet_names[0]
            payment_dfs.append(pd.read_excel(f, sheet_name=sheet_name))
        else:
            payment_dfs.append(pd.read_csv(f))
    
    payment_df = pd.concat(payment_dfs, ignore_index=True)
    st.sidebar.success(f"✅ {len(payment_files)} payment file(s)")

    # ── Column detection ────────────────────────────────────────────────
    tc = list(trips_df.columns)
    pc = list(payment_df.columns)
    
    def det(opts, cols):
        for o in opts:
            if o in cols: return o
        return None
    
    TRIP_ID  = det(["Trip ID", "TripID"], tc)
    LOAD_ID  = det(["Load ID", "LoadID"], tc)
    STATUS   = det(["Load Execution Status", "Status"], tc)
    DRIVER   = det(["Driver Name", "DriverName", "Driver"], tc)
    ROUTE    = det(["Facility Sequence", "Route"], tc)
    CDATE    = det(["Stop 2 Actual Arrival Date", "Stop 2  Actual Arrival Date"], tc)
    CTIME    = det(["Stop 2 Actual Arrival Time", "Stop 2  Actual Arrival Time"], tc)
    PAY_TRIP = det(["Trip ID", "TripID"], pc)
    PAY_LOAD = det(["Load ID", "LoadID"], pc)
    GROSS_PAY = det(["Gross Pay", "GrossPay", "Amount"], pc)

    # ── Build paid sets ───────────────────────────────────────────────
    paid_loads = set(payment_df[PAY_LOAD].dropna().astype(str).str.strip()) if PAY_LOAD else set()
    paid_trips = set(payment_df[PAY_TRIP].dropna().astype(str).str.strip()) if PAY_TRIP else set()

    # ── Payment week selector ──────────────────────────────────────────
    st.sidebar.subheader("📅 Payment Week (Amazon: Sun→Sat PT)")
    ws = st.sidebar.date_input("Week starts (Sunday)", value=pd.to_datetime("2026-05-17").date())
    we = st.sidebar.date_input("Week ends (Saturday)", value=ws + timedelta(days=6))

    # ── Build trip completion map ───────────────────────────────────────
    trip_latest_comp = {}
    load_comp_date = {}
    
    if CDATE:
        for _, row in trips_df.iterrows():
            lid = str(row[LOAD_ID] or "").strip()
            tid = str(row[TRIP_ID] or "").strip() if TRIP_ID else ""
            dt = parse_date(row[CDATE], row[CTIME] if CTIME else "")
            if dt is not None and lid:
                load_comp_date[lid] = dt
                if tid:
                    cur = trip_latest_comp.get(tid)
                    trip_latest_comp[tid] = dt if (cur is None or dt > cur) else cur

    # ── Classify loads ─────────────────────────────────────────────────
    truly_missing = []
    next_week = []
    cancelled = []
    
    for _, row in trips_df.iterrows():
        lid = str(row[LOAD_ID] or "").strip()
        tid = str(row[TRIP_ID] or "").strip() if TRIP_ID else ""
        status = str(row[STATUS] or "").strip() if STATUS else ""
        if not lid: continue
        
        is_paid = lid in paid_loads or (tid and tid in paid_trips)
        if is_paid: continue
        
        if any(x in status for x in ["Cancel", "Rejected", "Not Started"]):
            cancelled.append((lid, tid, row))
        elif "Complete" in status:
            comp_dt = trip_latest_comp.get(tid) if tid else load_comp_date.get(lid)
            if comp_dt and comp_dt.date() > we:
                next_week.append((lid, tid, row, comp_dt))
            else:
                truly_missing.append((lid, tid, row, comp_dt))
        else:
            truly_missing.append((lid, tid, row, None))

    # ── Display ────────────────────────────────────────────────────────
    st.header("📊 Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Loads", len(trips_df))
    c2.metric("Paid", len(paid_loads))
    c3.metric("Truly Missing", len(truly_missing))
    c4.metric("Cancelled", len(cancelled))

    # Table 1: Truly Missing
    st.header("🚨 Truly Missing Loads")
    st.caption("Completed but not in payment file + delivered this week")
    if truly_missing:
        disp_rows = [{
            "Trip ID": tid,
            "Load ID": lid,
            "Driver": str(row[DRIVER] or "").strip() if DRIVER else "",
            "Route": str(row[ROUTE] or "").strip() if ROUTE else "",
            "Delivery": f"{row.get(CDATE,'')} {row.get(CTIME,'')}".strip() if CDATE else "",
            "Status": str(row[STATUS] or "").strip() if STATUS else ""
        } for lid, tid, row, _ in truly_missing]
        st.dataframe(pd.DataFrame(disp_rows), use_container_width=True, hide_index=True)
    else:
        st.success("✅ No missing loads!")
    
    # Table 2: Cancelled/Rejected
    st.header("❌ Cancelled/Rejected")
    st.caption("Not in payment file (expected)")
    if cancelled:
        disp_rows = [{
            "Trip ID": tid,
            "Load ID": lid,
            "Driver": str(row[DRIVER] or "").strip() if DRIVER else "",
            "Status": str(row[STATUS] or "").strip() if STATUS else ""
        } for lid, tid, row in cancelled]
        st.dataframe(pd.DataFrame(disp_rows), use_container_width=True, hide_index=True)
    else:
        st.info("ℹ️ No cancelled loads")
    
    # Table 3: Next Week
    st.header("⏭️ Next Week's Payment")
    st.caption("Completed but delivered after week end — will appear next week")
    if next_week:
        st.info(f"📅 {len(next_week)} load(s) rolling to next week")
        disp_rows = [{
            "Trip ID": tid,
            "Load ID": lid,
            "Driver": str(row[DRIVER] or "").strip() if DRIVER else "",
            "Route": str(row[ROUTE] or "").strip() if ROUTE else "",
            "Last Delivery": dt.strftime("%A %m/%d"),
            "Status": str(row[STATUS] or "").strip() if STATUS else ""
        } for lid, tid, row, dt in next_week]
        st.dataframe(pd.DataFrame(disp_rows), use_container_width=True, hide_index=True)
    else:
        st.info("ℹ️ No loads rolling to next week")

    # ── Analytics ──────────────────────────────────────────────────
    st.header("📈 Analytics")
    
    # Total paid (exclude last row = summary)
    total_paid_actual = 0
    if GROSS_PAY:
        df_no_last = payment_df.iloc[:-1]
        all_gross = df_no_last[GROSS_PAY].apply(safe_float).sum()
        total_paid_actual = all_gross / 2  # tour+load duplicates
    
    # Summary metrics
    col1, col2 = st.columns(2)
    col1.metric("💰 Total Paid", f"${total_paid_actual:,.2f}")
    cancel_rate = (len(cancelled)/len(trips_df)*100) if len(trips_df) > 0 else 0
    col2.metric("❌ Cancel Rate", f"{cancel_rate:.1f}%")

    # ── Export all 3 tables ───────────────────────────────────────────
    st.header("📥 Export")
    
    if truly_missing or next_week or cancelled:
        # Build export dataframes
        
        missing_df = pd.DataFrame([row for _, _, row, _ in truly_missing], columns=trips_df.columns) if truly_missing else pd.DataFrame()
        cancelled_df = pd.DataFrame([row for _, _, row in cancelled], columns=trips_df.columns) if cancelled else pd.DataFrame()
        next_week_df = pd.DataFrame([row for _, _, row, _ in next_week], columns=trips_df.columns) if next_week else pd.DataFrame()
        
        # Full report
        buf = io.StringIO()
        buf.write("TRULY MISSING LOADS\n")
        if not missing_df.empty:
            missing_df.to_csv(buf, index=False)
        else:
            buf.write("None\n")
        
        buf.write("\nCANCELLED/REJECTED LOADS\n")
        if not cancelled_df.empty:
            cancelled_df.to_csv(buf, index=False, header=False)
        else:
            buf.write("None\n")
        
        buf.write("\nNEXT WEEK LOADS\n")
        if not next_week_df.empty:
            next_week_df.to_csv(buf, index=False, header=False)
        else:
            buf.write("None\n")
        
        st.download_button("Download Full Report (CSV)", buf.getvalue(),
            file_name=f"reconciliation_{ws}.csv", mime="text/csv")
        
        # Separate exports
        c1, c2, c3 = st.columns(3)
        with c1:
            if not missing_df.empty:
                b1 = io.StringIO()
                missing_df.to_csv(b1, index=False)
                st.download_button("Missing Only", b1.getvalue(),
                    file_name=f"missing_{ws}.csv", mime="text/csv")
        with c2:
            if not cancelled_df.empty:
                b2 = io.StringIO()
                cancelled_df.to_csv(b2, index=False)
                st.download_button("Cancelled Only", b2.getvalue(),
                    file_name=f"cancelled_{ws}.csv", mime="text/csv")
        with c3:
            if not next_week_df.empty:
                b3 = io.StringIO()
                next_week_df.to_csv(b3, index=False)
                st.download_button("Next Week Only", b3.getvalue(),
                    file_name=f"next_week_{ws}.csv", mime="text/csv")
    else:
        st.info("No data to export.")

else:
    st.info("👈 Upload TRIPS.csv and Payment Details file")
