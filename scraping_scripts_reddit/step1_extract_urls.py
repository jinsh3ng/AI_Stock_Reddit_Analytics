import asyncio
import re
import os
import sys
import asyncpg
from dotenv import load_dotenv
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig, ProxyConfig

# STEP 1: EXTRACT URLS

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

scroll_script = """
async function scrollPage() {
    let previousHeight = document.body.scrollHeight;
    let noChangeCount = 0;
    const maxNoChange = 5;
    for (let i = 0; i < 50; i++) {
        window.scrollTo(0, document.body.scrollHeight);
        await new Promise(r => setTimeout(r, 3000));
        let currentHeight = document.body.scrollHeight;
        if (currentHeight === previousHeight) {
            noChangeCount++;
            if (noChangeCount >= maxNoChange) break;
        } else {
            noChangeCount = 0;
        }
        previousHeight = currentHeight;
    }
}
await scrollPage();
"""

def extract_posts(markdown_text):
    pattern = r'##\s+\[\s*([^\]]+?)\s*\]\(https://www\.reddit\.com(/r/([^/]+)/comments/[^)]+?)/?\)'
    posts = []
    seen = set()
    for match in re.finditer(pattern, markdown_text):
        title = match.group(1).strip()
        url_path = match.group(2)
        subreddit = match.group(3)
        full_url = f"https://www.reddit.com{url_path}"
        if full_url not in seen:
            seen.add(full_url)
            posts.append({"url": full_url, "subreddit": subreddit, "title": title})
    return posts


async def init_db(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reddit_posts_urls (
                id        SERIAL PRIMARY KEY,
                url       TEXT NOT NULL,
                subreddit TEXT,
                title     TEXT,
                use_case  TEXT,
                UNIQUE(url, use_case)
            );
        """)
    print("✓ Table reddit_posts_urls ready")


async def load_existing_urls(pool: asyncpg.Pool) -> set:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT url FROM reddit_posts_urls")
        return {row["url"] for row in rows}


async def insert_new_posts(pool: asyncpg.Pool, posts: list[dict]):
    if not posts:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO reddit_posts_urls (url, subreddit, title, use_case)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (url, use_case) DO NOTHING
        """, [(p["url"], p["subreddit"], p["title"], p["use_case"]) for p in posts])
    return len(posts)


async def scrape_single_query(crawler, query, run_config, max_retries=3):
    url_query = query.replace(' ', '+')
    search_url = f"https://www.reddit.com/search/?q={url_query}"
    print(f"\n{'='*60}\nQuery: {query}\nURL: {search_url}\n{'='*60}")

    for attempt in range(1, max_retries + 1):
        try:
            result = await crawler.arun(url=search_url, config=run_config)
            if not result.success:
                print(f"  ❌ Attempt {attempt}/{max_retries}: {result.error_message[:100]}")
                if attempt < max_retries:
                    wait = attempt * 30
                    print(f"  ⏳ Waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                return []
            posts = extract_posts(result.markdown)
            print(f"✓ Found {len(posts)} posts")
            return posts
        except Exception as e:
            print(f"  ❌ Attempt {attempt}/{max_retries} error: {str(e)[:100]}")
            if attempt < max_retries:
                wait = attempt * 30
                print(f"  ⏳ Waiting {wait}s...")
                await asyncio.sleep(wait)
    return []


async def main(queries=None, use_case="ai_stocks"):
    if queries is None:
        raw = os.getenv("AI_STOCKS_QUERIES", "")
        queries = [q.strip() for q in raw.split(",") if q.strip()]  # ← split here
    if isinstance(queries, str):
        queries = [queries]  # ← this only handles the case where a single string is passed in directly

    if not DATABASE_URL:
        print("❌ DATABASE_URL not set in .env")
        sys.exit(1)

    print("=" * 60)
    print("REDDIT URL SCRAPER — AI STOCKS")
    print("=" * 60)
    print(f"Use case    : {use_case}")
    print(f"Total queries: {len(queries)}")

    pool = await asyncpg.create_pool(DATABASE_URL)
    try:
        await init_db(pool)
        existing_urls = await load_existing_urls(pool)
        print(f"✓ Existing URLs in DB: {len(existing_urls)}")

        browser_config = BrowserConfig(
            headless=True, verbose=False,
            extra_args=[
                "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-gpu", "--disable-software-rasterizer", "--single-process",
            ],
        )
        run_config = CrawlerRunConfig(
            scan_full_page=True,
            js_code=[scroll_script],
            wait_until="networkidle",
            delay_before_return_html=float(os.getenv("CRAWL_DELAY_BEFORE_RETURN", "5.0")),
            page_timeout=int(os.getenv("CRAWL_PAGE_TIMEOUT", "120000")),
            proxy_config=ProxyConfig(
                server=os.getenv("PROXY_SERVER"),
                username=os.getenv("PROXY_USERNAME"),
                password=os.getenv("PROXY_PASSWORD"),
            ),
        )

        all_posts = []
        seen = set()

        async with AsyncWebCrawler(config=browser_config) as crawler:
            for i, query in enumerate(queries):
                posts = await scrape_single_query(crawler, query, run_config)
                for p in posts:
                    if p["url"] not in seen:
                        seen.add(p["url"])
                        all_posts.append(p)
                if query != queries[-1]:
                    print(f"  ⏳ Waiting 300s before next query...")
                    await asyncio.sleep(300)

        to_insert = [
            {**p, "use_case": use_case}
            for p in all_posts
            if p["url"] not in existing_urls
        ]
        await insert_new_posts(pool, to_insert)

        print(f"\n{'='*60}\nEXTRACTION COMPLETE")
        print(f"New rows inserted : {len(to_insert)}")
        print(f"Duplicates skipped: {len(all_posts) - len(to_insert)}")
        return len(to_insert)

    finally:
        await pool.close()


if __name__ == "__main__":
    extra_queries = sys.argv[1:] if len(sys.argv) > 1 else None
    asyncio.run(main(queries=extra_queries))