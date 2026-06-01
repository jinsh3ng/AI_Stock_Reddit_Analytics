import asyncio
import asyncpg
import pandas as pd
import numpy as np
import re
import time
import os
import sys
import io
from dotenv import load_dotenv
from openai import OpenAI
from rank_bm25 import BM25Okapi

# STEP 4: FILTER → CLASSIFY → STORE

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

DATABASE_URL = os.getenv("DATABASE_URL")
client       = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")

USE_CASE = "ai_stocks"

# ── BM25 keyword corpus ────────────────────────────────────────────
# Tickers + AI theme queries used to score relevance before LLM call.
BM25_QUERIES = [
    # Tickers
    "NVIDIA NVDA stock price",
    "AMD stock AI chip",
    "Microsoft MSFT AI investment",
    "Google GOOGL Gemini AI",
    "Meta AI Llama stock",
    "Amazon AWS AI AMZN",
    "Apple AAPL AI stock",
    "Tesla TSLA AI autonomy",
    "Palantir PLTR AI stock",
    "Broadcom AVGO AI networking",
    "ASML semiconductor lithography stock",
    "ARM Holdings AI chip IP",
    "Super Micro Computer SMCI AI server",
    "Intel INTC AI accelerator",
    "Qualcomm QCOM AI mobile",
    "Taiwan Semiconductor TSM foundry",
    "OpenAI IPO valuation",
    "Anthropic Claude AI funding",
    # AI investing themes
    "AI stocks buy invest reddit",
    "artificial intelligence ETF fund",
    "AI chip semiconductor demand supply",
    "GPU data center hyperscaler capex",
    "LLM inference training cost revenue",
    "generative AI monetization stock",
    "AI bubble overvalued tech stocks",
    "AI stock earnings beat analyst",
    "AI stock correction pullback buy dip",
    "AI revenue growth forecast 2025 2026",
    "options NVDA calls puts wallstreetbets",
    "AI stock portfolio allocation strategy",
    "AI startup funding valuation unicorn",
    "AI replacing jobs automation stock impact",
    "AI regulation policy risk stock",
    "AI energy power consumption data center stock",
    "Singapore investor AI stocks global",
]


# ── LLM classifier ─────────────────────────────────────────────────

def classify_post(text: str) -> tuple[int, str, int]:
    """
    Returns (is_relevant, sentiment, is_financial).

    is_relevant : 1 if post discusses AI stocks / companies in a stock market context
    sentiment   : bullish | bearish | neutral | mixed
    is_financial: always 0 (unused — kept for schema compatibility)
    """
    prompt = f"""Analyze this Reddit post/comment.

TASK 1 — Relevance:
Is this post about AI-related stocks or companies in a stock market context?
- YES (1): Mentions price, valuation, earnings, outlook, buy/sell/hold opinion,
  sector performance, or general market sentiment for any AI-related company
  (e.g. NVDA, AMD, MSFT, PLTR, TSMC, AVGO, OpenAI, Anthropic, or any AI
  chipmaker / cloud provider / AI software company)
- NO (0): Pure AI technology discussion with no stock market angle;
  unrelated topics; spam

TASK 2 — Sentiment (only if relevant, else neutral):
bullish  = Positive on the stock/sector, expects gains
bearish  = Negative on the stock/sector, expects decline
neutral  = Factual or no clear directional view
mixed    = Acknowledges both upside and downside

Post text:
{text}

Return ONLY two comma-separated values. No labels, no explanation.
Format: relevance,sentiment
Examples:
1,bullish
1,bearish
0,neutral
"""
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are a classifier. Return ONLY: relevance,sentiment. No labels, no colons, no explanation."},
                {"role": "user", "content": prompt},
            ],
            stream=False,
            temperature=0,
        )
        result = response.choices[0].message.content.strip()
        result = re.sub(r'[`<>]', '', result)
        parts  = [p.strip().lstrip(':') for p in result.split(',') if p.strip()]
        if len(parts) < 2:
            print(f"  ✗ Unexpected LLM output: {result!r}")
            return 0, 'neutral', 0
        is_relevant = int(parts[0])
        sentiment   = parts[1].lower() if is_relevant else 'neutral'
        return is_relevant, sentiment, 0
    except Exception as e:
        print(f"  ✗ Classification error: {e}")
        return 0, 'neutral', 0


def classify_dataframe(df: pd.DataFrame, text_column='full_text') -> pd.DataFrame:
    total = len(df)
    relevance_list, sentiment_list, financial_list = [], [], []
    print(f"\nClassifying {total} texts via DeepSeek...")

    for idx, text in enumerate(df[text_column]):
        is_relevant, sentiment, is_financial = classify_post(text)
        relevance_list.append(is_relevant)
        sentiment_list.append(sentiment)
        financial_list.append(is_financial)
        if (idx + 1) % 50 == 0:
            print(f"  Progress: {idx+1}/{total} ({(idx+1)/total*100:.1f}%)")
        time.sleep(0.1)

    df['is_relevant']         = relevance_list
    df['sentiment']           = sentiment_list
    df['is_financial_sector'] = financial_list  # always 0, kept for schema compatibility

    relevant = sum(relevance_list)
    print(f"\n=== RELEVANCE FILTER ===")
    print(f"Relevant  : {relevant} ({relevant/total*100:.1f}%)")
    print(f"Filtered  : {total - relevant}")

    if relevant > 0:
        df_rel = df[df['is_relevant'] == 1]
        print(f"\n=== SENTIMENT BREAKDOWN ===")
        print(df_rel['sentiment'].value_counts().to_string())

    return df


# ── Database helpers ────────────────────────────────────────────────

async def init_cleaned_table(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS cleaned_data_{USE_CASE} (
                id                  SERIAL PRIMARY KEY,
                date                TEXT,
                time                TEXT,
                url                 TEXT,
                subreddit           TEXT,
                content_type        TEXT,
                author_handle       TEXT,
                title               TEXT,
                body                TEXT,
                upvotes             INTEGER,
                comment_count       INTEGER,
                full_text           TEXT,
                relevance_score     REAL,
                is_relevant         INTEGER,
                sentiment           TEXT,
                is_financial_sector INTEGER
            );
        """)
    print(f"✓ Table cleaned_data_{USE_CASE} ready")


async def load_completed_urls(pool: asyncpg.Pool) -> set:
    async with pool.acquire() as conn:
        exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables WHERE table_name = $1
            )
        """, f"cleaned_data_{USE_CASE}")
        if not exists:
            return set()
        rows = await conn.fetch(f"SELECT DISTINCT url FROM cleaned_data_{USE_CASE}")
        return {row['url'] for row in rows}


async def insert_cleaned(pool: asyncpg.Pool, df: pd.DataFrame):
    if df.empty:
        return
    async with pool.acquire() as conn:
        await conn.executemany(f"""
            INSERT INTO cleaned_data_{USE_CASE}
                (date, time, url, subreddit, content_type, author_handle,
                 title, body, upvotes, comment_count, full_text,
                 relevance_score, is_relevant, sentiment, is_financial_sector)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        """, [
            (
                row.get('date'), row.get('time'), row.get('url'),
                row.get('subreddit'), row.get('content_type'), row.get('author_handle'),
                row.get('title'), row.get('body'),
                int(row['upvotes'])       if pd.notna(row.get('upvotes'))       else None,
                int(row['comment_count']) if pd.notna(row.get('comment_count')) else None,
                row.get('full_text'),
                float(row['relevance_score']) if pd.notna(row.get('relevance_score')) else None,
                int(row['is_relevant']),
                row.get('sentiment'),
                int(row['is_financial_sector']),
            )
            for _, row in df.iterrows()
        ])
    print(f"✓ Inserted {len(df)} rows into cleaned_data_{USE_CASE}")


# ── Main pipeline ───────────────────────────────────────────────────

async def main():
    if not DATABASE_URL:
        print("❌ DATABASE_URL not set in .env")
        sys.exit(1)

    raw_table     = "raw_data"              
    cleaned_table = f"cleaned_data_{USE_CASE}"

    print("=" * 60)
    print(f"STEP 4: FILTER → CLASSIFY → STORE ({USE_CASE})")
    print("=" * 60)

    pool = await asyncpg.create_pool(DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, date, time, url, subreddit, content_type,
                       author_handle, title, body, upvotes, comment_count
                FROM raw_data
                ORDER BY date DESC NULLS LAST, time DESC NULLS LAST
            """)
        df_raw       = pd.DataFrame([dict(r) for r in rows])
        total_loaded = len(df_raw)
        print(f"\n✓ Loaded {total_loaded} rows from {raw_table}")

        # Deduplicate
        dedup_cols = [c for c in df_raw.columns if c not in ('id', 'upvotes', 'comment_count')]
        df_raw.drop_duplicates(subset=dedup_cols, keep='first', inplace=True)
        df_raw.sort_values(by=['date', 'time'], inplace=True)
        df_raw.reset_index(drop=True, inplace=True)
        print(f"✓ After dedup: {len(df_raw)} rows")

        # No subreddit filter — AI stock discussion happens globally
        print("✓ Subreddit filter skipped (global coverage for AI stocks)")

        # Skip already-processed
        await init_cleaned_table(pool)
        completed_urls = await load_completed_urls(pool)
        before  = len(df_raw)
        df_raw  = df_raw[~df_raw['url'].isin(completed_urls)].reset_index(drop=True)
        print(f"✓ Already in cleaned table : {len(completed_urls)}")
        print(f"✓ Remaining to process     : {len(df_raw)} (skipped {before - len(df_raw)})")

        if df_raw.empty:
            print("\n✓ Nothing new to process!")
            return

        # BM25 relevance scoring
        df_raw['text'] = df_raw['title'].fillna('') + ' ' + df_raw['body'].fillna('')
        tokenized_corpus = [
            re.sub(r'[^a-z0-9\s]', ' ', str(t).lower()).split()
            for t in df_raw['text']
        ]
        bm25 = BM25Okapi(tokenized_corpus)
        print(f"✓ BM25 index built over {len(tokenized_corpus)} documents")

        all_scores = []
        for query in BM25_QUERIES:
            tq = re.sub(r'[^a-z0-9\s]', ' ', query.lower()).split()
            all_scores.append(bm25.get_scores(tq))

        df_raw['relevance_score'] = np.array(all_scores).max(axis=0)
        df_filtered = df_raw[df_raw['relevance_score'] > 0].copy()
        df_filtered.sort_values('relevance_score', ascending=False, inplace=True)
        df_filtered.reset_index(drop=True, inplace=True)
        print(f"✓ After BM25 filter (score > 0): {len(df_filtered)} rows")

        if df_filtered.empty:
            print("\n✓ No new relevant posts after BM25 filtering.")
            return

        # LLM classification
        df_filtered['full_text'] = df_filtered['title'].fillna('') + ' ' + df_filtered['body'].fillna('')
        df_filtered = classify_dataframe(df_filtered)

        # Store
        df_out = df_filtered.drop(columns=['text', 'id'], errors='ignore')
        await insert_cleaned(pool, df_out)

        relevant_count  = df_out['is_relevant'].sum()
        financial_count = df_out[df_out['is_relevant'] == 1]['is_financial_sector'].sum()

        print(f"\n{'='*60}\nSTEP 4 COMPLETE")
        print(f"Raw rows loaded       : {total_loaded}")
        print(f"After BM25 filter     : {len(df_filtered)}")
        print(f"LLM-relevant posts    : {relevant_count}")
        print(f"With fin. instruments : {financial_count}")
        print(f"Saved to              : postgres → {cleaned_table}")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())