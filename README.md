# V-Ads.txt-Validator

A Streamlit app to validate ads.txt coverage across domains and demand partners.

---

## 📁 File Structure

```
your-project/
│
├── app.py                    ← Main Streamlit application
├── requirements.txt          ← Python dependencies
├── README.md                 ← This file
│
└── database/                 ← Created automatically on first run
    ├── adsdata.db            ← SQLite DB (auto-generated)
    └── App-Demand Ops Ads.txt coverage (2).xlsx   ← ⚠️ Place this manually
```

---

## ⚙️ Setup

### 1. Clone / download the project

### 2. Place your Excel file
Put your Excel file inside the `database/` folder (create it if it doesn't exist):
```
database/App-Demand Ops Ads.txt coverage (2).xlsx
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the app
```bash
streamlit run app.py
```

The SQLite database (`adsdata.db`) will be created automatically on the first run by reading your Excel file.

---

## 📊 Excel File Format Expected

| Column Index | Sheet 0 (Master) | Other Sheets (Partners) |
|---|---|---|
| 0 | Domain | — |
| 3 | Account Manager | — |
| 4 | — | Integration Type |
| 6 | Master Lines | Partner Lines |

- **Sheet 0**: Master domain list
- **Sheets 1 to N-1**: One sheet per demand partner
- **Last sheet**: Ignored

---

## 🚀 Features

- Validate ads.txt coverage for multiple domains at once
- Check against multiple demand partners simultaneously
- See coverage % with color-coded results (green/yellow/red)
- Expand missing lines per domain/partner
- Export full report + missing lines to Excel
- Add new domains from the sidebar
- Results cached for performance

---

## 🛠 Resetting the Database

To re-import from Excel (e.g. after updating the Excel file):
```bash
rm database/adsdata.db
```
Then rerun the app — it will regenerate the DB from your Excel file.
