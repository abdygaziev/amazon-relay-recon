# RelayRecon 🚛

Amazon Relay trip reconciliation app — finds missing payments by comparing trip data with payment details.

## Features

- 📊 **Smart Reconciliation** - Automatically matches trips with payments
- 🚨 **Missing Payment Detection** - Identifies completed loads not in payment files
- ⏭️ **Next Week Prediction** - Shows loads rolling to next payment cycle
- ❌ **Cancelled Load Tracking** - Separates expected non-payments
- 📈 **Analytics** - Total paid amount and cancel rate metrics
- 📥 **Export Reports** - Download CSV reports for missing, cancelled, and next week loads

## Installation

```bash
# Clone the repo
git clone https://github.com/yourusername/RelayRecon.git
cd RelayRecon

# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run app.py
```

## Usage

1. **Upload TRIPS.csv** - Trip data from Amazon Relay
2. **Upload Payment Details** - Excel file (with "Payment Details" sheet) or CSV
3. **Select Payment Week** - Sunday to Saturday range
4. **Review Results** - Missing, cancelled, and next week loads
5. **Export Reports** - Download CSV files as needed

## File Format

### TRIPS.csv
Required columns: `Load ID`, `Trip ID`, `Driver Name`, `Load Execution Status`, `Stop 2 Actual Arrival Date`, `Stop 2 Actual Arrival Time`

### Payment Details
Supported formats: `.csv`, `.xlsx`, `.xls`

Required columns: `Load ID` or `Trip ID`, `Gross Pay`

## Deployment

### Streamlit Community Cloud
1. Push to GitHub
2. Visit [share.streamlit.io](https://share.streamlit.io)
3. Connect repo and deploy

### Fly.io
```bash
fly launch --no-deploy
fly deploy
```

## Tech Stack

- **Streamlit** - UI framework
- **Pandas** - Data processing
- **OpenPyXL** - `.xlsx` Excel file support
- **xlrd** - legacy `.xls` Excel file support

## License

MIT License

---

**Built for Amazon Relay carriers** 🚛
