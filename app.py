from datetime import date, timedelta
import io

import pandas as pd
import streamlit as st

from reconcile import ValidationError, reconcile


st.set_page_config(page_title="RelayRecon", page_icon="🚛", layout="wide")

st.markdown(
    """
<style>
    .block-container { padding-top: 1rem; padding-bottom: 0rem; }

    .stMetric {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 15px;
        border-radius: 10px;
        color: white;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .stMetric > div { color: white; }
    .stMetric label { color: rgba(255,255,255,0.9) !important; }

    h1 { color: #1E3A8A; margin-bottom: 0.5rem; }
    h2 { color: #374151; margin-top: 1rem; margin-bottom: 0.5rem; }
    h3 { color: #4B5563; margin-top: 0.75rem; }

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

    .stDataFrame {
        font-size: 13px;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
    }

    .stDownloadButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 10px 20px;
        font-weight: 600;
    }

    .stSuccess { border-left: 4px solid #10B981; }
    .stError { border-left: 4px solid #EF4444; }
    .stWarning { border-left: 4px solid #F59E0B; }
    .stInfo { border-left: 4px solid #3B82F6; }

    .dataframe { font-size: 12px !important; }
    .dataframe th {
        background-color: #374151;
        color: white;
        font-weight: 600;
    }
    .dataframe tr:nth-child(even) { background-color: #F9FAFB; }
</style>
""",
    unsafe_allow_html=True,
)

st.title("🚛 RelayRecon")
st.caption("Amazon Relay trip reconciliation — finds missing payments")


def default_week_start(today: date | None = None) -> date:
    today = today or date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    return today - timedelta(days=days_since_sunday)


def infer_week_start(payment_df: pd.DataFrame) -> date:
    for column in ["End Date", "Payment End Date", "Week End", "Work Period End"]:
        if column not in payment_df.columns:
            continue
        parsed_dates = pd.to_datetime(payment_df[column], errors="coerce").dropna()
        if parsed_dates.empty:
            continue
        week_end = parsed_dates.max().date()
        days_since_sunday = (week_end.weekday() + 1) % 7
        return week_end - timedelta(days=days_since_sunday)
    return default_week_start()


def read_payment_file(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith((".xlsx", ".xls")):
        engine = "xlrd" if name.endswith(".xls") else "openpyxl"
        xl = pd.ExcelFile(uploaded_file, engine=engine)
        sheet_name = "Payment Details" if "Payment Details" in xl.sheet_names else None
        if sheet_name is None:
            detail_sheets = [sheet for sheet in xl.sheet_names if "detail" in sheet.lower()]
            sheet_name = detail_sheets[0] if detail_sheets else xl.sheet_names[0]
        uploaded_file.seek(0)
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, engine=engine)
    return pd.read_csv(uploaded_file)


def display_table(title: str, caption: str, df: pd.DataFrame, empty_message: str, success: bool = False) -> None:
    st.header(title)
    st.caption(caption)
    if df.empty:
        if success:
            st.success(empty_message)
        else:
            st.info(empty_message)
        return

    columns = [
        "Normalized Trip ID",
        "Normalized Load ID",
        "Driver Display",
        "Route Display",
        "Completion Datetime",
        "Status Display",
    ]
    visible_columns = [column for column in columns if column in df.columns]
    display_df = df[visible_columns].rename(
        columns={
            "Normalized Trip ID": "Trip ID",
            "Normalized Load ID": "Load ID",
            "Driver Display": "Driver",
            "Route Display": "Route",
            "Completion Datetime": "Delivery",
            "Status Display": "Status",
        }
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)


def display_completed_table(df: pd.DataFrame) -> None:
    st.header("✅ Completed Trips")
    st.caption("Completed trip/load rows with reconciliation status")

    if df.empty:
        st.info("No completed trips found.")
        return

    driver_options = sorted(driver for driver in df["Driver Display"].dropna().unique() if driver)
    selected_drivers = st.multiselect("Driver", driver_options, placeholder="All drivers")
    filtered_df = df[df["Driver Display"].isin(selected_drivers)] if selected_drivers else df

    columns = [
        "Normalized Trip ID",
        "Normalized Load ID",
        "Driver Display",
        "Route Display",
        "Completion Datetime",
        "Reconciliation Status",
    ]
    display_df = filtered_df[columns].rename(
        columns={
            "Normalized Trip ID": "Trip ID",
            "Normalized Load ID": "Load ID",
            "Driver Display": "Driver",
            "Route Display": "Route",
            "Completion Datetime": "Completion",
            "Reconciliation Status": "Payment Status",
        }
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)


def full_report_csv(result) -> str:
    sections = [
        ("TRULY MISSING LOADS", result.missing_df),
        ("CANCELLED/REJECTED LOADS", result.cancelled_df),
        ("NEXT WEEK LOADS", result.next_week_df),
    ]
    buffer = io.StringIO()
    for index, (title, df) in enumerate(sections):
        if index:
            buffer.write("\n")
        buffer.write(f"{title}\n")
        if df.empty:
            buffer.write("None\n")
        else:
            df.to_csv(buffer, index=False)
    return buffer.getvalue()


st.sidebar.header("Upload Files")
trips_file = st.sidebar.file_uploader("Amazon Relay History", type="csv", key="trips")
payment_files = st.sidebar.file_uploader(
    "Payment Details (Excel or CSV)",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=True,
    key="payment",
)

if not trips_file or not payment_files:
    st.info("👈 Upload Amazon Relay History and Payment Details files")
    st.stop()

try:
    trips_df = pd.read_csv(trips_file)
except Exception as exc:
    st.error(f"Could not read Amazon Relay History file: {exc}")
    st.stop()

payment_dfs = []
for payment_file in payment_files:
    try:
        payment_dfs.append(read_payment_file(payment_file))
    except Exception as exc:
        st.error(f"Could not read payment file {payment_file.name}: {exc}")
        st.stop()

payment_df = pd.concat(payment_dfs, ignore_index=True) if payment_dfs else pd.DataFrame()
st.sidebar.success(f"✅ {len(payment_files)} payment file(s)")

st.sidebar.subheader("📅 Payment Week (Amazon: Sun→Sat PT)")
inferred_week_start = infer_week_start(payment_df)
week_start = st.sidebar.date_input("Week starts (Sunday)", value=inferred_week_start)
week_end = st.sidebar.date_input("Week ends (Saturday)", value=week_start + timedelta(days=6))

try:
    result = reconcile(trips_df, payment_df, week_end)
except ValidationError as exc:
    st.error(str(exc))
    st.stop()

if result.warnings:
    for warning in result.warnings:
        st.warning(warning)

st.header("📊 Summary")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Loads", len(result.trips_df))
c2.metric("Paid IDs", result.paid_load_count + result.paid_trip_count)
c3.metric("Truly Missing", len(result.missing_df))
c4.metric("Cancelled", len(result.cancelled_df))

with st.expander("Diagnostics"):
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Detected Trip Columns")
        st.dataframe(
            pd.DataFrame(
                [{"Field": key, "Column": value or "Not found"} for key, value in result.detected_trip_columns.items()]
            ),
            use_container_width=True,
            hide_index=True,
        )
    with col2:
        st.subheader("Detected Payment Columns")
        st.dataframe(
            pd.DataFrame(
                [{"Field": key, "Column": value or "Not found"} for key, value in result.detected_payment_columns.items()]
            ),
            use_container_width=True,
            hide_index=True,
        )
    st.subheader("File Checks")
    st.dataframe(
        pd.DataFrame([{"Check": key, "Value": value} for key, value in result.diagnostics.items()]),
        use_container_width=True,
        hide_index=True,
    )

display_table(
    "🚨 Truly Missing Loads",
    "Completed but not in payment file + trip/tour completes this week",
    result.missing_df,
    "✅ No missing loads!",
    success=True,
)

display_table(
    "❌ Cancelled/Rejected",
    "Not in payment file (expected)",
    result.cancelled_df,
    "ℹ️ No cancelled loads",
)

display_table(
    "⏭️ Next Week's Payment",
    "Trip/tour completes after week end — entire trip shifts to next week",
    result.next_week_df,
    "ℹ️ No loads rolling to next week",
)

display_completed_table(result.completed_df)

st.header("📈 Analytics")
col1, col2 = st.columns(2)
col1.metric("💰 Total Paid", f"${result.total_paid:,.2f}")
col2.metric("❌ Cancel Rate", f"{result.cancel_rate:.1f}%")

st.header("📥 Export")
if result.missing_df.empty and result.cancelled_df.empty and result.next_week_df.empty:
    st.info("No data to export.")
else:
    st.download_button(
        "Download Full Report (CSV)",
        full_report_csv(result),
        file_name=f"reconciliation_{week_start}.csv",
        mime="text/csv",
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        if not result.missing_df.empty:
            buffer = io.StringIO()
            result.missing_df.to_csv(buffer, index=False)
            st.download_button("Missing Only", buffer.getvalue(), file_name=f"missing_{week_start}.csv", mime="text/csv")
    with c2:
        if not result.cancelled_df.empty:
            buffer = io.StringIO()
            result.cancelled_df.to_csv(buffer, index=False)
            st.download_button(
                "Cancelled Only",
                buffer.getvalue(),
                file_name=f"cancelled_{week_start}.csv",
                mime="text/csv",
            )
    with c3:
        if not result.next_week_df.empty:
            buffer = io.StringIO()
            result.next_week_df.to_csv(buffer, index=False)
            st.download_button(
                "Next Week Only",
                buffer.getvalue(),
                file_name=f"next_week_{week_start}.csv",
                mime="text/csv",
            )
