import asyncio
import io
import os
import sys
import asyncpg
from dotenv import load_dotenv

# STEP 3: UPDATE DATE IN reddit_posts_urls FROM raw_data

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

DATABASE_URL = os.getenv("DATABASE_URL")
USE_CASE     = "ai_stocks"


async def main():
    if not DATABASE_URL:
        print("❌ DATABASE_URL not set in .env")
        sys.exit(1)

    print("=" * 60)
    print("STEP 3: UPDATE reddit_posts_urls WITH POST DATES")
    print("=" * 60)
    print(f"Source table : raw_data (forum_post only)")
    print(f"Use case     : {USE_CASE}")

    pool = await asyncpg.create_pool(DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                ALTER TABLE reddit_posts_urls
                ADD COLUMN IF NOT EXISTS date TEXT;
            """)
            print("✓ Column 'date' ready on reddit_posts_urls")

            result = await conn.execute("""
                UPDATE reddit_posts_urls AS target
                SET date = source.date
                FROM (
                    SELECT DISTINCT ON (RTRIM(url, '/')) RTRIM(url, '/') AS url_clean, date
                    FROM raw_data
                    WHERE content_type = 'forum_post'
                ) AS source
                WHERE RTRIM(target.url, '/') = source.url_clean
                  AND target.use_case = $1;
            """, USE_CASE)

            rows_updated = int(result.split(" ")[-1])
            total  = await conn.fetchval(
                "SELECT COUNT(*) FROM reddit_posts_urls WHERE use_case = $1", USE_CASE
            )
            filled = await conn.fetchval(
                "SELECT COUNT(*) FROM reddit_posts_urls WHERE use_case = $1 AND date IS NOT NULL", USE_CASE
            )

            print(f"\n✓ Updated {rows_updated} rows")
            print(f"  Total URLs         : {total}")
            print(f"  With date filled   : {filled}")
            print(f"  Still missing date : {total - filled}")

    finally:
        await pool.close()

    print(f"\n{'='*60}\nDONE\n{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())