import asyncio
import json
import io
import os
import sys
import asyncpg
from datetime import datetime, timedelta
from dotenv import load_dotenv
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig, ProxyConfig
from parser import extract_reddit_data

# STEP 2: EXTRACT CONTENT FROM URLS

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

# ── Config ──────────────────────────────────────────────
BATCH_SIZE             = int(os.getenv("BATCH_SIZE", "25"))
BATCH_DELAY            = int(os.getenv("BATCH_DELAY", "60"))
DELAY_BETWEEN_REQUESTS = int(os.getenv("DELAY_BETWEEN_REQUESTS", "2"))
DATE_CUTOFF            = os.getenv("DATE_CUTOFF", "2023-01-01")
TABLE_NAME             = "raw_data"
# ────────────────────────────────────────────────────────


def normalize_url(url):
    """Strip .json and trailing slash for consistent comparison."""
    return url.replace('/.json', '').replace('.json', '').rstrip('/')


def clean_reddit_url(url):
    """Add .json to URL if not already present."""
    if url.endswith('.json'):
        return url
    base_url = url.split('?')[0]
    if not base_url.endswith('/'):
        base_url += '/'
    return f"{base_url}.json"


# ─── Database helpers ────────────────────────────────────────────────

async def init_raw_table(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id            SERIAL PRIMARY KEY,
                date          TEXT,
                time          TEXT,
                url           TEXT,
                subreddit     TEXT,
                content_type  TEXT,
                author_handle TEXT,
                title         TEXT,
                body          TEXT,
                upvotes       INTEGER,
                comment_count INTEGER
            );
        """)
    print(f"✓ Table {TABLE_NAME} ready")


async def load_source_urls(pool: asyncpg.Pool) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT url FROM reddit_posts_urls")
        return [row["url"] for row in rows]


async def load_completed_urls(pool: asyncpg.Pool) -> set:
    """Load already-scraped URLs from the raw data table (implicit progress)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT DISTINCT url FROM {TABLE_NAME}")
        return {normalize_url(row["url"]) for row in rows}


async def insert_records(pool: asyncpg.Pool, records: list[dict]) -> int:
    """Bulk-insert extracted records. Only inserts records on or after DATE_CUTOFF."""
    if not records:
        return 0
    filtered = [r for r in records if r.get("date") and r["date"] >= DATE_CUTOFF]
    if not filtered:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(f"""
            INSERT INTO {TABLE_NAME}
                (date, time, url, subreddit, content_type,
                 author_handle, title, body, upvotes, comment_count)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """, [
            (
                r.get("date"), r.get("time"), r.get("url"), r.get("subreddit"),
                r.get("content_type"), r.get("author_handle"), r.get("title"),
                r.get("body"), r.get("upvotes"), r.get("comment_count"),
            )
            for r in filtered
        ])
    return len(filtered)


# ─── Main ────────────────────────────────────────────────────────────

async def main():
    if not DATABASE_URL:
        print("❌ DATABASE_URL not set in .env")
        sys.exit(1)

    print("=" * 60)
    print("REDDIT DATA EXTRACTOR — CRAWL4AI + POSTGRES")
    print("=" * 60)
    print(f"Target table: {TABLE_NAME}")
    print(f"Date cutoff : >= {DATE_CUTOFF}")

    pool = await asyncpg.create_pool(DATABASE_URL)
    try:
        await init_raw_table(pool)

        # ── Load source URLs and determine pending ──
        source_urls    = await load_source_urls(pool)
        completed_urls = await load_completed_urls(pool)
        pending_urls   = [u for u in source_urls if normalize_url(u) not in completed_urls]
        total_urls     = len(pending_urls)

        print(f"✓ Source URLs       : {len(source_urls)}")
        print(f"✓ Already completed : {len(completed_urls)}")
        print(f"✓ Remaining         : {total_urls}")

        if not pending_urls:
            print("\n✓ All URLs already scraped!")
            return

        # Clean URLs (add .json)
        pending_urls  = [clean_reddit_url(u) for u in pending_urls]
        batches       = [pending_urls[i:i + BATCH_SIZE] for i in range(0, total_urls, BATCH_SIZE)]
        total_batches = len(batches)

        print(f"\n📦 Configuration:")
        print(f"   Total URLs    : {total_urls}")
        print(f"   Batch size    : {BATCH_SIZE}")
        print(f"   Delay/request : {DELAY_BETWEEN_REQUESTS}s")
        print(f"   Delay/batch   : {BATCH_DELAY}s\n")

        total_records = total_skipped = total_successful = total_failed = processed = 0
        start_time    = datetime.now()

        browser_config = BrowserConfig(
            extra_args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
            ],
        )
        run_config = CrawlerRunConfig(
            wait_until="networkidle",
            delay_before_return_html=float(os.getenv("CRAWL_DELAY_BEFORE_RETURN", "5.0")),
            page_timeout=int(os.getenv("CRAWL_PAGE_TIMEOUT", "120000")),
            proxy_config=ProxyConfig(
                server=os.getenv("PROXY_SERVER"),
                username=os.getenv("PROXY_USERNAME"),
                password=os.getenv("PROXY_PASSWORD"),
            ),
        )

        async with AsyncWebCrawler(config=browser_config) as crawler:
            for batch_num, batch_urls in enumerate(batches, 1):
                print(f"\n{'='*60}")
                print(f"BATCH {batch_num}/{total_batches} — {len(batch_urls)} URLs — {datetime.now().strftime('%H:%M:%S')}")
                print(f"{'='*60}")

                batch_successful = batch_failed = 0
                batch_records = []

                for url in batch_urls:
                    processed += 1
                    url_short = url.split('/comments/')[1][:50] if '/comments/' in url else url[:50]
                    print(f"[{processed}/{total_urls}] {url_short}")

                    try:
                        result = await crawler.arun(url=url, config=run_config)

                        if not result.success:
                            print(f"  ✗ Failed to load")
                            batch_failed += 1
                            total_failed += 1
                            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
                            continue

                        # Extract raw text and strip ``` fences (including ```json)
                        raw_text = (result.html or result.markdown or "").strip()

                        # Try to find JSON array in the response
                        import re
                        json_match = re.search(r'(\[.*\])', raw_text, re.DOTALL)
                        if json_match:
                            raw_text = json_match.group(1)
                        else:
                            # fallback: strip markdown fences
                            if raw_text.startswith("```"):
                                raw_text = raw_text.split("\n", 1)[1]
                            if raw_text.endswith("```"):
                                raw_text = raw_text[:-3]
                            raw_text = raw_text.strip()

                        json_data = json.loads(raw_text)

                        extracted_records = extract_reddit_data(json_data)
                        if extracted_records:
                            batch_records.extend(extracted_records)
                            print(f"  ✓ {len(extracted_records)} records extracted")
                            batch_successful += 1
                            total_successful += 1
                        else:
                            print(f"  ⚠️  No records extracted")
                            batch_failed += 1
                            total_failed += 1

                    except json.JSONDecodeError as e:
                        print(f"  ✗ Failed to parse JSON: {raw_text[:100]!r}")
                        batch_failed += 1
                        total_failed += 1
                    except Exception as e:
                        print(f"  ✗ Error: {e}")
                        batch_failed += 1
                        total_failed += 1

                    await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

                # ── Insert batch into Postgres (date-filtered) ──
                extracted_count = len(batch_records)
                inserted_count  = await insert_records(pool, batch_records)
                skipped_count   = extracted_count - inserted_count
                total_records  += inserted_count
                total_skipped  += skipped_count

                print(f"\n  Batch {batch_num} — ✓ {batch_successful} / ✗ {batch_failed}")
                print(f"  💾 Inserted {inserted_count} records ({skipped_count} skipped, pre-{DATE_CUTOFF[:4]})")
                print(f"  💾 Checkpoint — {total_records} total records in {TABLE_NAME}")

                if batch_num < total_batches:
                    print(f"  ⏳ Waiting {BATCH_DELAY}s before next batch...")
                    await asyncio.sleep(BATCH_DELAY)

        # ── Final summary ──
        total_duration = (datetime.now() - start_time).total_seconds()
        print(f"\n{'='*60}\nFINAL SUMMARY\n{'='*60}")
        print(f"Total URLs    : {total_urls}")
        print(f"Successful    : {total_successful} ({total_successful/total_urls*100:.1f}%)")
        print(f"Failed        : {total_failed} ({total_failed/total_urls*100:.1f}%)")
        print(f"Records insert: {total_records}")
        print(f"Records skip  : {total_skipped} (pre-{DATE_CUTOFF[:4]})")
        print(f"Total Time    : {total_duration//60:.0f}m {total_duration%60:.0f}s")
        print(f"Avg per URL   : {total_duration/total_urls:.1f}s")
        print(f"✓ Saved to    : postgres → {TABLE_NAME}")
        print(f"{'='*60}\n")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())