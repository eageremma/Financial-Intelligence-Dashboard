import re
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from statsmodels.tsa.holtwinters import ExponentialSmoothing

# ==========================================================
# PERSONAL CASH FLOW INTELLIGENCE PIPELINE
# ==========================================================
# Purpose:
#   1) Load and clean a bank statement export
#   2) Standardize dates and transaction status
#   3) Create recruiter-friendly analysis fields
#   4) Segment spending into meaningful categories
#   5) Detect recurring payments
#   6) Detect unusual / anomalous spending
#   7) Forecast next month's inflow / outflow
#   8) Export tidy tables for Power BI
#
# How to use:
#   - Update INPUT_FILE if needed
#   - Run this script in Python
#   - Load the exported CSV files into Power BI
# ==========================================================

INPUT_FILE = Path('/mnt/data/account-statement_2025-03-01_2026-03-09_en-gb_bf30b9.csv')
OUTPUT_DIR = Path('/mnt/data/cashflow_diy/outputs')
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------
# STEP 1: LOAD RAW DATA
# ----------------------------------------------------------
raw = pd.read_csv(INPUT_FILE)


# ----------------------------------------------------------
# STEP 2: STANDARD CLEANING
# ----------------------------------------------------------
# Clean column names just in case there are stray spaces.
raw.columns = [c.strip() for c in raw.columns]

# Convert date columns to datetime.
for col in ['Started Date', 'Completed Date']:
    raw[col] = pd.to_datetime(raw[col], errors='coerce')

# Numeric safety conversion.
for col in ['Amount', 'Fee', 'Balance']:
    raw[col] = pd.to_numeric(raw[col], errors='coerce')

# Choose the best operational date for analysis.
raw['analysis_date'] = raw['Completed Date'].fillna(raw['Started Date'])

# Keep only rows with a valid usable date.
raw = raw[raw['analysis_date'].notna()].copy()

# Use only completed rows for "actual cash movement" analysis.
df = raw[raw['State'].astype(str).str.upper() == 'COMPLETED'].copy()

# Remove exact duplicates if any exist.
df = df.drop_duplicates().copy()


# ----------------------------------------------------------
# STEP 3: FEATURE ENGINEERING
# ----------------------------------------------------------
df['year'] = df['analysis_date'].dt.year
df['month_num'] = df['analysis_date'].dt.month
df['month_name'] = df['analysis_date'].dt.month_name()
df['year_month'] = df['analysis_date'].dt.to_period('M').astype(str)
df['quarter'] = df['analysis_date'].dt.to_period('Q').astype(str)
df['day_name'] = df['analysis_date'].dt.day_name()
df['day_of_month'] = df['analysis_date'].dt.day
df['week_of_year'] = df['analysis_date'].dt.isocalendar().week.astype(int)
df['is_weekend'] = df['analysis_date'].dt.dayofweek >= 5

# Build inflow/outflow helper columns.
df['flow_direction'] = np.where(df['Amount'] >= 0, 'Inflow', 'Outflow')
df['inflow_abs'] = np.where(df['Amount'] > 0, df['Amount'], 0.0)
df['outflow_abs'] = np.where(df['Amount'] < 0, -df['Amount'], 0.0)

# Standardize text fields for easier matching.
df['description_clean'] = (
    df['Description']
    .fillna('')
    .astype(str)
    .str.strip()
)
df['description_lower'] = df['description_clean'].str.lower()
df['type_clean'] = df['Type'].fillna('').astype(str).str.strip()


# ----------------------------------------------------------
# STEP 4: CATEGORY ENGINE
# ----------------------------------------------------------
# This is rule-based categorization. It is simple, transparent, and easy to explain.
# You can expand or adjust these keyword rules as you learn more about your own data.
CATEGORY_RULES = {
    'Transport': [
        'arriva', 'trainline', 'uber', 'stagecoach', 'transport for london',
        'rail', 'northern', 'first bus', 'flixbus', 'lothian', 'bee network',
        'lime street', 'james street', 'tram', 'bus', 'coach'
    ],
    'Groceries & Household': [
        'tesco', 'aldi', 'sainsbury', 'lidl', 'co-op', 'go local', 'home bargains',
        'b&m', 'fairways', 'groceries', 'supermarket', 'daily store', 'convenience'
    ],
    'Eating Out': [
        'greggs', 'nando', 'mcdonald', 'kebab', 'starbucks', 'pret', 'tim hortons',
        'takea', 'archie', 'bakery', 'roast', 'too good to go', 'restaurant', 'cafe'
    ],
    'Shopping & Personal': [
        'boohoo', 'depop', 'aliexpress', 'temu', 'boots', 'ryman', 'primark',
        'footasylum', 'lookfantastic', 'fragrance', 'perfume', 'cosmetics',
        'fashion', 'souvenirs', 'whsmith', 'creamstore'
    ],
    'Subscriptions & Services': [
        'coursera', 'booking.com', 'use ai', 'selecta', 'sauna', 'subscription',
        'spotify', 'netflix', 'amazon prime', 'icloud', 'google one', 'microsoft'
    ],
    'Cash / ATM / Fees': [
        'cash withdrawal', 'atm', 'cash', 'fee', 'charge', 'bank charge'
    ]
}

ESSENTIAL_CATEGORIES = {
    'Transport', 'Groceries & Household', 'Housing & Bills', 'Healthcare', 'Cash / ATM / Fees'
}

DISCRETIONARY_CATEGORIES = {
    'Eating Out', 'Shopping & Personal', 'Entertainment', 'Subscriptions & Services', 'Travel & Leisure'
}


def categorize_transaction(description: str, txn_type: str, amount: float) -> str:
    d = str(description).lower().strip()
    t = str(txn_type).lower().strip()

    # Inflow side first.
    if amount > 0:
        if 'payment from' in d or t == 'topup':
            return 'Income / Topups'
        if 'refund' in t or 'refund' in d:
            return 'Refunds'
        if t == 'exchange':
            return 'Exchange / Adjustment'
        return 'Other Inflows'

    # Obvious transfers.
    if t == 'transfer' and d.startswith('to '):
        return 'Transfers Out'
    if d.startswith('to '):
        return 'Transfers Out'

    # Keyword rules.
    for category, keywords in CATEGORY_RULES.items():
        if any(k in d for k in keywords):
            return category

    return 'Other Spending'


# Apply category engine.
df['category'] = df.apply(
    lambda row: categorize_transaction(row['description_clean'], row['type_clean'], row['Amount']),
    axis=1
)


# ----------------------------------------------------------
# STEP 5: HIGHER-LEVEL CLASSIFICATIONS
# ----------------------------------------------------------
def classify_spend_nature(category: str, amount: float) -> str:
    if amount >= 0:
        return 'Not Applicable'
    if category in ESSENTIAL_CATEGORIES:
        return 'Essential'
    if category in DISCRETIONARY_CATEGORIES:
        return 'Discretionary'
    if category == 'Transfers Out':
        return 'Transfer'
    return 'Other'


def classify_fixed_variable(description: str, category: str, txn_type: str) -> str:
    d = str(description).lower().strip()
    t = str(txn_type).lower().strip()
    fixed_keywords = ['subscription', 'rent', 'broadband', 'insurance', 'council tax', 'coursera']

    if category == 'Transfers Out':
        return 'Transfer'
    if any(k in d for k in fixed_keywords):
        return 'Fixed'
    if t in ['card payment', 'cash withdrawal', 'transfer']:
        return 'Variable'
    return 'Variable'


df['spend_nature'] = df.apply(
    lambda row: classify_spend_nature(row['category'], row['Amount']), axis=1
)
df['cost_behavior'] = df.apply(
    lambda row: classify_fixed_variable(row['description_clean'], row['category'], row['type_clean']), axis=1
)


# ----------------------------------------------------------
# STEP 6: MERCHANT STANDARDIZATION
# ----------------------------------------------------------
# Bank exports often contain messy merchant strings. This light cleaning helps group repeats.
def standardize_merchant_name(description: str) -> str:
    d = str(description).strip()
    d = re.sub(r'\s+', ' ', d)
    d = re.sub(r'\*+', '', d)
    return d


df['merchant_standardized'] = df['description_clean'].apply(standardize_merchant_name)


# ----------------------------------------------------------
# STEP 7: CREATE MONTHLY CASH FLOW TABLE
# ----------------------------------------------------------
monthly_cashflow = (
    df.groupby('year_month', as_index=False)
      .agg(
          inflow=('inflow_abs', 'sum'),
          outflow=('outflow_abs', 'sum'),
          fees=('Fee', 'sum'),
          transactions=('Amount', 'size'),
          ending_balance=('Balance', 'last')
      )
)
monthly_cashflow['net_cash_flow'] = monthly_cashflow['inflow'] - monthly_cashflow['outflow']
monthly_cashflow['savings_rate'] = np.where(
    monthly_cashflow['inflow'] > 0,
    monthly_cashflow['net_cash_flow'] / monthly_cashflow['inflow'],
    np.nan
)
monthly_cashflow['expense_to_income_ratio'] = np.where(
    monthly_cashflow['inflow'] > 0,
    monthly_cashflow['outflow'] / monthly_cashflow['inflow'],
    np.nan
)
monthly_cashflow['rolling_3m_outflow_avg'] = monthly_cashflow['outflow'].rolling(3).mean()
monthly_cashflow['rolling_3m_inflow_avg'] = monthly_cashflow['inflow'].rolling(3).mean()


# ----------------------------------------------------------
# STEP 8: SPENDING SEGMENTATION TABLES
# ----------------------------------------------------------
category_summary = (
    df.groupby('category', as_index=False)
      .agg(
          total_inflow=('inflow_abs', 'sum'),
          total_outflow=('outflow_abs', 'sum'),
          transactions=('Amount', 'size')
      )
      .sort_values('total_outflow', ascending=False)
)
category_summary['share_of_total_outflow'] = np.where(
    category_summary['total_outflow'].sum() > 0,
    category_summary['total_outflow'] / category_summary['total_outflow'].sum(),
    0.0
)

merchant_summary = (
    df[df['Amount'] < 0]
      .groupby('merchant_standardized', as_index=False)
      .agg(
          total_spend=('outflow_abs', 'sum'),
          transactions=('Amount', 'size'),
          avg_transaction=('outflow_abs', 'mean')
      )
      .sort_values('total_spend', ascending=False)
)

category_monthly = (
    df[df['Amount'] < 0]
      .groupby(['year_month', 'category'], as_index=False)
      .agg(total_outflow=('outflow_abs', 'sum'))
)

nature_summary = (
    df[df['Amount'] < 0]
      .groupby('spend_nature', as_index=False)
      .agg(total_outflow=('outflow_abs', 'sum'))
)

cost_behavior_summary = (
    df[df['Amount'] < 0]
      .groupby('cost_behavior', as_index=False)
      .agg(total_outflow=('outflow_abs', 'sum'))
)


# ----------------------------------------------------------
# STEP 9: RECURRING PAYMENT ANALYSIS
# ----------------------------------------------------------
outgoing = df[df['Amount'] < 0].copy()
outgoing['year_month_period'] = pd.PeriodIndex(outgoing['year_month'], freq='M')

recurring_payments = (
    outgoing.groupby('merchant_standardized', as_index=False)
            .agg(
                months_present=('year_month', 'nunique'),
                transactions=('Amount', 'size'),
                total_spend=('outflow_abs', 'sum'),
                avg_spend=('outflow_abs', 'mean'),
                median_spend=('outflow_abs', 'median'),
                first_seen=('analysis_date', 'min'),
                last_seen=('analysis_date', 'max'),
                category=('category', lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0])
            )
)

# You can tune this threshold. 4+ months is a good practical starting point.
recurring_payments = recurring_payments[recurring_payments['months_present'] >= 4].copy()
recurring_payments['is_likely_recurring'] = np.where(
    recurring_payments['months_present'] >= 6, 'Yes', 'Maybe'
)
recurring_payments = recurring_payments.sort_values(
    ['months_present', 'total_spend'], ascending=[False, False]
)


# ----------------------------------------------------------
# STEP 10: ANOMALY DETECTION
# ----------------------------------------------------------
# We exclude Transfers Out because they can swamp the distribution and make the anomaly logic less meaningful.
non_transfer_outflows = df[(df['Amount'] < 0) & (df['category'] != 'Transfers Out')].copy()

q1 = non_transfer_outflows['outflow_abs'].quantile(0.25)
q3 = non_transfer_outflows['outflow_abs'].quantile(0.75)
iqr = q3 - q1
anomaly_threshold = q3 + 1.5 * iqr

anomalies = non_transfer_outflows[non_transfer_outflows['outflow_abs'] > anomaly_threshold].copy()
anomalies = anomalies.sort_values('outflow_abs', ascending=False)
anomalies['anomaly_flag'] = 'High spend outlier'
anomalies['anomaly_threshold'] = anomaly_threshold


# ----------------------------------------------------------
# STEP 11: BEHAVIOURAL PATTERN TABLES
# ----------------------------------------------------------
day_of_week_spend = (
    df[df['Amount'] < 0]
      .groupby('day_name', as_index=False)
      .agg(total_outflow=('outflow_abs', 'sum'))
)

day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
day_of_week_spend['day_name'] = pd.Categorical(day_of_week_spend['day_name'], categories=day_order, ordered=True)
day_of_week_spend = day_of_week_spend.sort_values('day_name')

# Position within month: beginning / middle / end.
df['month_position'] = pd.cut(
    df['day_of_month'],
    bins=[0, 10, 20, 31],
    labels=['Start of month', 'Middle of month', 'End of month'],
    include_lowest=True
)

month_position_spend = (
    df[df['Amount'] < 0]
      .groupby('month_position', as_index=False)
      .agg(total_outflow=('outflow_abs', 'sum'))
)


# ----------------------------------------------------------
# STEP 12: FORECASTING
# ----------------------------------------------------------
# Forecast only on complete months. Partial months can distort the signal.
monthly_cashflow['year_month_period'] = pd.PeriodIndex(monthly_cashflow['year_month'], freq='M')
last_period = monthly_cashflow['year_month_period'].max()

# Treat the latest month as potentially partial if the statement ends before month-end.
# Safer approach: exclude the max month from training.
train_months = monthly_cashflow[monthly_cashflow['year_month_period'] < last_period].copy()

forecast_table = pd.DataFrame()

if len(train_months) >= 6:
    inflow_series = train_months.set_index('year_month_period')['inflow']
    outflow_series = train_months.set_index('year_month_period')['outflow']

    inflow_model = ExponentialSmoothing(
        inflow_series,
        trend='add',
        seasonal=None,
        initialization_method='estimated'
    ).fit()

    outflow_model = ExponentialSmoothing(
        outflow_series,
        trend='add',
        seasonal=None,
        initialization_method='estimated'
    ).fit()

    next_inflow = float(inflow_model.forecast(1).iloc[0])
    next_outflow = float(outflow_model.forecast(1).iloc[0])

    next_period = (last_period + 1).strftime('%Y-%m')
    forecast_table = pd.DataFrame({
        'forecast_month': [next_period],
        'forecast_inflow': [next_inflow],
        'forecast_outflow': [next_outflow],
        'forecast_net_cash_flow': [next_inflow - next_outflow]
    })

# Optional “core spend” forecast excluding transfers.
core_monthly_spend = (
    df[(df['Amount'] < 0) & (df['category'] != 'Transfers Out')]
      .groupby('year_month', as_index=False)
      .agg(core_outflow=('outflow_abs', 'sum'))
)
core_monthly_spend['year_month_period'] = pd.PeriodIndex(core_monthly_spend['year_month'], freq='M')
core_train = core_monthly_spend[core_monthly_spend['year_month_period'] < core_monthly_spend['year_month_period'].max()].copy()

core_forecast_table = pd.DataFrame()
if len(core_train) >= 6:
    core_model = ExponentialSmoothing(
        core_train.set_index('year_month_period')['core_outflow'],
        trend='add',
        seasonal=None,
        initialization_method='estimated'
    ).fit()
    core_next = float(core_model.forecast(1).iloc[0])
    core_next_period = (core_monthly_spend['year_month_period'].max() + 1).strftime('%Y-%m')
    core_forecast_table = pd.DataFrame({
        'forecast_month': [core_next_period],
        'forecast_core_outflow': [core_next]
    })


# ----------------------------------------------------------
# STEP 13: RECRUITER-FRIENDLY KPI TABLE
# ----------------------------------------------------------
kpis = pd.DataFrame({
    'metric': [
        'Total completed transactions',
        'Date range start',
        'Date range end',
        'Total inflow',
        'Total outflow',
        'Net cash flow',
        'Average monthly inflow',
        'Average monthly outflow',
        'Average savings rate',
        'Anomaly threshold'
    ],
    'value': [
        len(df),
        str(df['analysis_date'].min().date()),
        str(df['analysis_date'].max().date()),
        df['inflow_abs'].sum(),
        df['outflow_abs'].sum(),
        df['inflow_abs'].sum() - df['outflow_abs'].sum(),
        train_months['inflow'].mean() if len(train_months) else np.nan,
        train_months['outflow'].mean() if len(train_months) else np.nan,
        train_months['savings_rate'].mean() if 'savings_rate' in train_months.columns and len(train_months) else np.nan,
        anomaly_threshold if len(non_transfer_outflows) else np.nan
    ]
})


# ----------------------------------------------------------
# STEP 14: EXPORT TIDY TABLES FOR POWER BI
# ----------------------------------------------------------
df.to_csv(OUTPUT_DIR / 'transactions_cleaned.csv', index=False)
monthly_cashflow.to_csv(OUTPUT_DIR / 'monthly_cashflow.csv', index=False)
category_summary.to_csv(OUTPUT_DIR / 'category_summary.csv', index=False)
category_monthly.to_csv(OUTPUT_DIR / 'category_monthly.csv', index=False)
merchant_summary.to_csv(OUTPUT_DIR / 'merchant_summary.csv', index=False)
recurring_payments.to_csv(OUTPUT_DIR / 'recurring_payments.csv', index=False)
anomalies.to_csv(OUTPUT_DIR / 'anomalies.csv', index=False)
day_of_week_spend.to_csv(OUTPUT_DIR / 'day_of_week_spend.csv', index=False)
month_position_spend.to_csv(OUTPUT_DIR / 'month_position_spend.csv', index=False)
nature_summary.to_csv(OUTPUT_DIR / 'spend_nature_summary.csv', index=False)
cost_behavior_summary.to_csv(OUTPUT_DIR / 'cost_behavior_summary.csv', index=False)
forecast_table.to_csv(OUTPUT_DIR / 'forecast_table.csv', index=False)
core_forecast_table.to_csv(OUTPUT_DIR / 'core_forecast_table.csv', index=False)
kpis.to_csv(OUTPUT_DIR / 'kpis.csv', index=False)


# ----------------------------------------------------------
# STEP 15: OPTIONAL PYTHON CHARTS FOR QA / PREVIEW
# ----------------------------------------------------------
# These are not required for Power BI, but they help you quickly validate the outputs.
if len(monthly_cashflow) > 0:
    plt.figure(figsize=(11, 5))
    plt.plot(monthly_cashflow['year_month'], monthly_cashflow['inflow'], marker='o', label='Inflow')
    plt.plot(monthly_cashflow['year_month'], monthly_cashflow['outflow'], marker='o', label='Outflow')
    plt.plot(monthly_cashflow['year_month'], monthly_cashflow['net_cash_flow'], marker='o', label='Net Cash Flow')
    plt.xticks(rotation=45)
    plt.title('Monthly Cash Flow')
    plt.ylabel('Amount')
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'qa_monthly_cashflow.png', dpi=150)
    plt.close()

if len(category_summary) > 0:
    temp = category_summary[category_summary['total_outflow'] > 0].head(10).sort_values('total_outflow')
    plt.figure(figsize=(10, 5))
    plt.barh(temp['category'], temp['total_outflow'])
    plt.title('Top Spending Categories')
    plt.xlabel('Amount')
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'qa_category_spend.png', dpi=150)
    plt.close()

print('Pipeline completed successfully.')
print(f'Outputs saved to: {OUTPUT_DIR}')
