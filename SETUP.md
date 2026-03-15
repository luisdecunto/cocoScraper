# Setup Guide

## What you must do manually

### 1. Install PostgreSQL
Download and install from https://www.postgresql.org/download/windows/

During installation, set a password for the `postgres` superuser. Remember it.

### 2. Create the database and user

Open **pgAdmin** or **psql** and run:

```sql
CREATE DATABASE prices;
CREATE USER scraper WITH PASSWORD 'choose_a_password';
GRANT ALL PRIVILEGES ON DATABASE prices TO scraper;
\c prices
GRANT ALL ON SCHEMA public TO scraper;
```

### 3. Create your `.env` file

Copy `.env.example` to `.env` and fill in the values:

```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=prices
DB_USER=scraper
DB_PASS=        ← the password you chose above

MAXICONSUMO_USER=   ← your Maxiconsumo login email
MAXICONSUMO_PASS=   ← your Maxiconsumo login password
```

### 4. Install Python dependencies

The project requires **Python 3.12**. Run from the `cocoScraper/` folder:

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows
pip install -r requirements.txt
```

---

## What I can do for you (once you give me the info below)

- Run `python -m scraper.main db init` to create all tables
- Run a test scrape on a single category to verify login works
- Run the full scrape
- Run the exports and open the dashboard
- Debug any errors that come up
- Add new suppliers
- Adjust selectors if the site changes

---

## Information I need from you

| What | Why |
|---|---|
| Your Maxiconsumo login email | To authenticate and get supplier-tier ("categorizado") prices |
| Your Maxiconsumo login password | Same |
| The PostgreSQL password you chose | So the scraper can connect to the DB |
| Confirmation that `python -m scraper.main db init` ran without errors | Before we can scrape anything |

You don't need to share credentials with me directly — just put them in `.env` and tell me "done". I'll run the next steps from there.

---

## First run sequence (after setup)

```bash
python -m scraper.main db init
python -m scraper.main scrape --supplier maxiconsumo
python -m scraper.main export latest
streamlit run dashboard/app.py
```
