from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from openai import OpenAI
import pandas as pd
import os
import asyncio
import asyncpg
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import json, re
from dotenv import load_dotenv
import yfinance as yf
from rank_bm25 import BM25Okapi

# ── Sentence transformer (load once) ──
model_st = SentenceTransformer("all-MiniLM-L6-v2")

# ── Topic classification cache ──
_topics_cache: dict = {}

# ── Data cache ──
_df_cache: dict = {}

CANDIDATE_LABELS = []
N_TOPICS    = 5
SAMPLE_SIZE = 100

app = FastAPI(title="AI Stock Reddit Analytics API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Paths ──
BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, "..", ".env"), override=True)

DATABASE_URL = os.getenv("DATABASE_URL")

# ── Lazy DeepSeek client ──
_deepseek_client = None

def get_deepseek() -> OpenAI:
    global _deepseek_client
    if _deepseek_client is None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=503,
                detail="DEEPSEEK_API_KEY not configured.",
            )
        _deepseek_client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
    return _deepseek_client

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# ── Date range filter ──
DATE_RANGES = {
    "Last 3 months": 90,
    "Last 6 months": 180,
    "All time":      None,
}


# ─────────────────────────────────────────
#  Startup: load data from Postgres
# ─────────────────────────────────────────
async def load_data_from_db():
    global _df_cache
    pool = await asyncpg.create_pool(DATABASE_URL)
    try:
        async with pool.acquire() as conn:
            exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'cleaned_data_ai_stocks'
                )
            """)
            if not exists:
                print("⚠ Table cleaned_data_ai_stocks does not exist")
                _df_cache["ai_stocks"] = pd.DataFrame()
                return

            rows = await conn.fetch("""
                SELECT date, time, url, subreddit, content_type,
                       author_handle, title, body, upvotes, comment_count,
                       full_text, relevance_score, is_relevant,
                       sentiment, is_financial_sector
                FROM cleaned_data_ai_stocks
                WHERE is_relevant = 1
            """)

        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df[df["date"] >= pd.Timestamp("2025-05-01")]
        df = df.dropna(subset=["sentiment"])
        df = df[df["sentiment"].str.strip() != ""]
        df["_source"] = "reddit"
        _df_cache["ai_stocks"] = df
        print(f"✓ Loaded {len(df)} rows from cleaned_data_ai_stocks")

    finally:
        await pool.close()


@app.on_event("startup")
async def startup_event():
    await load_data_from_db()


# ─────────────────────────────────────────
#  POST /api/refresh
# ─────────────────────────────────────────
@app.post("/api/refresh")
async def refresh_data():
    global _topics_cache
    _topics_cache = {}
    await load_data_from_db()
    total = sum(len(df) for df in _df_cache.values())
    return {"status": "ok", "message": f"Reloaded {total} rows from database"}


# ─────────────────────────────────────────
#  Pydantic models
# ─────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    use_case:     str = "AI Stocks"
    date_range:   str = "All time"
    sort_by:      str = "upvotes"
    dataset:      str = "all"
    group_by:     str = "quarterly"
    stock_filter: str = ""


class GenAIRequest(BaseModel):
    volume_data:  list
    use_case:     str  = "AI Stocks"
    date_range:   str  = "All time"
    question:     str  = ""
    mode:         str  = "volume"
    topics_data:  list = []
    stock_filter: str  = ""


class TopicsRequest(BaseModel):
    dataset:        str = "all"
    date_range:     str = "All time"
    quarter_filter: str = ""
    use_case:       str = "AI Stocks"
    stock_filter:   str = ""


class TopicsPostsRequest(BaseModel):
    dataset:        str = "all"
    date_range:     str = "All time"
    sort_by:        str = "upvotes"
    quarter_filter: str = ""
    use_case:       str = "AI Stocks"
    stock_filter:   str = ""


class SubtopicsRequest(BaseModel):
    dataset:        str = "all"
    date_range:     str = "All time"
    topic:          str = ""
    sort_by:        str = "upvotes"
    quarter_filter: str = ""
    use_case:       str = "AI Stocks"
    stock_filter:   str = ""


class EmergingRequest(BaseModel):
    dataset:        str = "all"
    date_range:     str = "All time"
    quarter_filter: str = ""
    use_case:       str = "AI Stocks"
    stock_filter:   str = ""


class HealthRequest(BaseModel):
    date_range: str = "All time"
    dataset:    str = "all"
    use_case:   str = "AI Stocks"


class StockChartRequest(BaseModel):
    ticker:     str = ""
    date_range: str = "All time"


class UniqueAuthorsRequest(BaseModel):
    use_case:     str = "AI Stocks"
    date_range:   str = "All time"
    dataset:      str = "all"
    group_by:     str = "quarterly"
    stock_filter: str = ""


# ─────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────
def filter_by_stock(df: pd.DataFrame, stock_filter: str) -> pd.DataFrame:
    """BM25 filter posts by stock name/ticker relevance."""
    if not stock_filter or stock_filter.strip() == "":
        return df

    query = stock_filter.strip().lower()
    # Build query variants: e.g. "nvidia" -> ["nvidia", "nvda"] if user typed either
    query_tokens = re.sub(r"[^a-z0-9\s]", " ", query).split()

    texts = df["full_text"].fillna("").tolist()
    tokenized_corpus = [
        re.sub(r"[^a-z0-9\s]", " ", str(t).lower()).split()
        for t in texts
    ]

    # Handle empty corpus
    if not any(tokenized_corpus):
        return df

    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(query_tokens)

    # Keep posts with score > 0 (at least one query token matches)
    mask = scores > 0
    filtered = df[mask].copy()

    # If nothing matches, return all (graceful fallback)
    if len(filtered) == 0:
        return df

    return filtered


def normalize_stock_filter(stock_filter: str) -> str:
    return stock_filter.strip().lower()


def load_and_filter(date_range: str, dataset: str = "all", use_case: str = "AI Stocks") -> pd.DataFrame:
    df = _df_cache.get("ai_stocks", pd.DataFrame()).copy()

    if df.empty:
        raise HTTPException(status_code=503, detail="No data loaded for AI Stocks.")

    if date_range.startswith("Custom:"):
        parts = date_range.split(":")
        if len(parts) == 3:
            date_from = pd.to_datetime(parts[1], errors="coerce")
            date_to   = pd.to_datetime(parts[2], errors="coerce")
            if pd.notna(date_from): df = df[df["date"] >= date_from]
            if pd.notna(date_to):   df = df[df["date"] <= date_to + pd.Timedelta(days=1)]
    else:
        days = DATE_RANGES.get(date_range)
        if days:
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
            df = df[df["date"] >= cutoff]

    return df


def apply_quarter_filter(df: pd.DataFrame, quarter_filter: str) -> pd.DataFrame:
    df = df.copy()
    df["year"]          = df["date"].dt.year
    df["quarter"]       = df["date"].dt.quarter
    df["quarter_label"] = df["year"].astype(str) + "Q" + df["quarter"].astype(str)
    if quarter_filter:
        df = df[df["quarter_label"] == quarter_filter]
    return df


def discover_topics(texts: list, existing: list) -> list:
    sample = "\n\n".join(
        [f"[DOC_{i}]: {t[:300]}" for i, t in enumerate(texts[:SAMPLE_SIZE])]
    )
    existing_str = "\n".join([f"- {l}" for l in existing]) if existing else "None"
    prompt = f"""You are analyzing Reddit posts about AI stocks and investing.

Example posts:
{sample}

Existing topics already identified:
{existing_str}

Identify {N_TOPICS} DISTINCT topics that appear in multiple posts and are specific to AI stock investing.
Do NOT use the word "and". Do NOT create topics similar to existing ones.

Return ONLY raw JSON array: ["Topic 1", "Topic 2", ...]"""

    response = get_deepseek().chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "You are a topic analyst. Return only a raw JSON array, no markdown."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.3,
    )
    raw    = response.choices[0].message.content.strip()
    raw    = re.sub(r'^```(?:json)?\s*', '', raw)
    raw    = re.sub(r'\s*```$', '', raw)
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, list) else next(iter(parsed.values()))


def get_embeddings(texts: list) -> np.ndarray:
    return model_st.encode(texts, show_progress_bar=False)


def classify_topics(df: pd.DataFrame):
    texts = df["full_text"].fillna("").tolist()

    discovered = discover_topics(texts, CANDIDATE_LABELS)
    all_topics = CANDIDATE_LABELS + discovered

    topic_embeddings = get_embeddings(all_topics)

    text_embeddings = []
    for i in range(0, len(texts), 100):
        text_embeddings.append(get_embeddings(texts[i : i + 100]))
    text_embeddings = np.vstack(text_embeddings)

    sim_matrix = cosine_similarity(text_embeddings, topic_embeddings)

    records = []
    for sims in sim_matrix:
        top3_idx    = np.argsort(sims)[::-1][:3]
        top3_scores = sims[top3_idx]
        top3_scores = top3_scores / top3_scores.sum()
        records.append({
            "llm_topic": all_topics[top3_idx[0]],
        })

    df = df.copy()
    df["llm_topic"] = [r["llm_topic"] for r in records]
    return df, all_topics


# ─────────────────────────────────────────
#  Serializers
# ─────────────────────────────────────────
def serialize_row(row: pd.Series) -> dict:
    return {
        "date":          str(row["date"])[:10],
        "upvotes":       int(row["upvotes"])       if pd.notna(row.get("upvotes"))       else 0,
        "comment_count": int(row["comment_count"]) if pd.notna(row.get("comment_count")) else 0,
        "full_text":     str(row.get("full_text", "")),
        "title":         str(row.get("title", ""))         if pd.notna(row.get("title"))         else "",
        "author":        str(row.get("author_handle", "")) if pd.notna(row.get("author_handle")) else "unknown",
        "url":           str(row.get("url", "")),
        "subreddit":     str(row.get("subreddit", "")),
        "sentiment":     str(row.get("sentiment", "neutral")),
        "_source":       "reddit",
    }


def get_top_posts(subset: pd.DataFrame, sort_by: str, n: int = 10) -> pd.DataFrame:
    if sort_by == "date":
        return subset.sort_values("date", ascending=False).head(n)
    return subset.sort_values("upvotes", ascending=False).head(n)


# ─────────────────────────────────────────
#  GET / → serve index.html
# ─────────────────────────────────────────
@app.get("/")
def read_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# ─────────────────────────────────────────
#  GET /health
# ─────────────────────────────────────────
@app.get("/health")
def health():
    df = _df_cache.get("ai_stocks", pd.DataFrame())
    if df.empty:
        return {"status": "ok", "message": "No data loaded", "earliest_date": "Unknown", "latest_date": "Unknown"}

    earliest = df["date"].min()
    latest   = df["date"].max()
    return {
        "status":        "ok",
        "message":       "AI Stock Reddit Analytics API is running",
        "total_rows":    len(df),
        "earliest_date": earliest.strftime("%d %b %Y") if pd.notna(earliest) else "Unknown",
        "latest_date":   latest.strftime("%d %b %Y")   if pd.notna(latest)   else "Unknown",
    }


# ─────────────────────────────────────────
#  POST /api/coverage
# ─────────────────────────────────────────
@app.post("/api/coverage")
def get_coverage(req: HealthRequest):
    try:
        df = load_and_filter(req.date_range, req.dataset, req.use_case)
        if df.empty:
            return {"status": "ok", "earliest_date": "—", "latest_date": "—"}
        earliest = df["date"].min()
        latest   = df["date"].max()
        return {
            "status":        "ok",
            "earliest_date": earliest.strftime("%d %b %Y") if pd.notna(earliest) else "—",
            "latest_date":   latest.strftime("%d %b %Y")   if pd.notna(latest)   else "—",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /api/genai
# ─────────────────────────────────────────
@app.post("/api/genai")
def get_genai(req: GenAIRequest):
    try:
        system_prompt = """You are a financial analyst specializing in AI stocks sentiment analysis.
You analyze Reddit posts to understand retail investor sentiment on AI-related stocks.
Be concise, data-driven, and professional. Use bullish/bearish framing where appropriate."""

        if req.mode == "topics":
            topics_summary = "\n".join([
                f"  {d['topic']}: {d['count']} posts ({d['pct']}%)"
                for d in req.topics_data
            ])
            if req.question:
                user_prompt = f"""Topic breakdown from Reddit posts about AI Stocks:
Date range: {req.date_range}

{topics_summary}

User question: {req.question}

Answer concisely using the data above."""
            else:
                stock_note = f"\nStock filter: {req.stock_filter}" if req.stock_filter else ""
                user_prompt = f"""Topic breakdown from Reddit posts about AI Stocks:
Date range: {req.date_range}{stock_note}

{topics_summary}

Write a short analytical summary (3-5 sentences) covering:
1. The most dominant topics and what they reveal about investor concerns
2. Any surprising or notable topic distributions
3. What this suggests about current AI stock market sentiment"""

        else:
            data_summary = "".join([
                f"  {d['period_str']}: {d['post_count']} posts "
                f"(Bearish: {d['neg_count']}, Mixed: {d['mix_count']}, "
                f"Neutral: {d['neu_count']}, Bullish: {d['pos_count']})\n"
                for d in req.volume_data
            ])
            if req.question:
                user_prompt = f"""AI Stocks Reddit sentiment data:
Date range: {req.date_range}

{data_summary}

User question: {req.question}

Answer concisely."""
            else:
                user_prompt = f"""AI Stocks Reddit sentiment data:
Date range: {req.date_range}

{data_summary}

Write a short analytical summary (3-5 sentences) covering:
1. Overall volume trend (growing, declining, stable?)
2. Dominant sentiment and how it changed over time
3. Any notable spikes or drops worth highlighting
4. What this means for AI stock investor sentiment"""

        response = get_deepseek().chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=400,
        )

        analysis = response.choices[0].message.content.strip()
        return {"status": "ok", "analysis": analysis}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /api/volume
# ─────────────────────────────────────────
@app.post("/api/volume")
def get_volume(req: AnalyzeRequest):
    try:
        df = load_and_filter(req.date_range, req.dataset, req.use_case)
        df = filter_by_stock(df, normalize_stock_filter(req.stock_filter))

        def fmt_daily(d):     return d.strftime("%Y-%m-%d")
        def fmt_monthly(d):   return d.strftime("%Y-%b")
        def fmt_quarterly(d): return f"{d.year}Q{d.quarter}"
        def fmt_yearly(d):    return str(d.year)

        FREQ_MAP = {
            "daily":     ("D",  fmt_daily),
            "monthly":   ("ME", fmt_monthly),
            "quarterly": ("QE", fmt_quarterly),
            "yearly":    ("YE", fmt_yearly),
        }
        freq, fmt = FREQ_MAP.get(req.group_by, FREQ_MAP["quarterly"])

        quarterly_stats = (
            df
            .set_index("date")
            .resample(freq)
            .agg(
                post_count   = ("sentiment", "count"),
                pct_negative = ("sentiment", lambda x: (x == "bearish").mean()),
                pct_mixed    = ("sentiment", lambda x: (x == "mixed").mean()),
                pct_neutral  = ("sentiment", lambda x: (x == "neutral").mean()),
                pct_positive = ("sentiment", lambda x: (x == "bullish").mean()),
            )
            .reset_index()
        )

        quarterly_stats["period_str"] = quarterly_stats["date"].apply(fmt)
        quarterly_stats["post_count"] = quarterly_stats["post_count"].fillna(0)
        quarterly_stats["neg_count"]  = (quarterly_stats["pct_negative"].fillna(0) * quarterly_stats["post_count"]).round().astype(int)
        quarterly_stats["mix_count"]  = (quarterly_stats["pct_mixed"].fillna(0)    * quarterly_stats["post_count"]).round().astype(int)
        quarterly_stats["neu_count"]  = (quarterly_stats["pct_neutral"].fillna(0)  * quarterly_stats["post_count"]).round().astype(int)
        quarterly_stats["pos_count"]  = (quarterly_stats["pct_positive"].fillna(0) * quarterly_stats["post_count"]).round().astype(int)

        result = quarterly_stats[[
            "period_str", "post_count",
            "neg_count", "mix_count", "neu_count", "pos_count",
        ]].to_dict(orient="records")

        return {"status": "ok", "data": result}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /api/topics
# ─────────────────────────────────────────
@app.post("/api/topics")
def get_topics(req: TopicsRequest):
    try:
        cache_key = f"{req.use_case}|{req.dataset}|{req.date_range}|{req.quarter_filter}|{normalize_stock_filter(req.stock_filter)}"

        if cache_key not in _topics_cache:
            df = load_and_filter(req.date_range, req.dataset, req.use_case)
            df = filter_by_stock(df, normalize_stock_filter(req.stock_filter))
            df = apply_quarter_filter(df, req.quarter_filter)

            if len(df) == 0:
                _topics_cache[cache_key] = {
                    "topics":              [],
                    "counts":              [],
                    "sentiment_breakdown": {},
                    "df_classified":       df,
                }
            else:
                df_classified, _ = classify_topics(df)
                df_plot = df_classified[df_classified["llm_topic"].notna()].copy()

                tw = df_plot["llm_topic"].value_counts().sort_values(ascending=True)

                sentiments = ["bearish", "mixed", "neutral", "bullish"]
                topic_sentiment = pd.crosstab(df_plot["llm_topic"], df_plot["sentiment"])
                topic_sentiment = topic_sentiment.reindex(
                    index=tw.index,
                    columns=[s for s in sentiments if s in topic_sentiment.columns],
                    fill_value=0,
                )

                _topics_cache[cache_key] = {
                    "topics": tw.index.tolist(),
                    "counts": tw.values.tolist(),
                    "sentiment_breakdown": {
                        s: topic_sentiment[s].tolist() if s in topic_sentiment.columns
                        else [0] * len(tw)
                        for s in sentiments
                    },
                    "df_classified": df_plot,
                }

        cached = _topics_cache[cache_key]

        df_all = load_and_filter(req.date_range, req.dataset, req.use_case)
        df_all = apply_quarter_filter(df_all, "")
        quarters = sorted(df_all["quarter_label"].unique().tolist())  # quarters show all data regardless of stock filter

        return {
            "status": "ok",
            "data": {
                "topics":              cached["topics"],
                "counts":              cached["counts"],
                "sentiment_breakdown": cached["sentiment_breakdown"],
                "quarters":            quarters,
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /api/topics/posts
# ─────────────────────────────────────────
@app.post("/api/topics/posts")
def get_topics_posts(req: TopicsPostsRequest):
    try:
        cache_key = f"{req.use_case}|{req.dataset}|{req.date_range}|{req.quarter_filter}|{normalize_stock_filter(req.stock_filter)}"

        if cache_key not in _topics_cache:
            raise HTTPException(status_code=400, detail="Run /api/topics first (cache miss)")

        df_classified = _topics_cache[cache_key].get("df_classified")
        if df_classified is None or len(df_classified) == 0:
            return {"status": "ok", "data": [], "total": 0}

        total  = len(df_classified)
        topics = df_classified["llm_topic"].value_counts().index.tolist()

        result = []
        for topic in topics:
            subset = df_classified[df_classified["llm_topic"] == topic]
            count  = len(subset)
            pct    = round(count / total * 100, 1)
            top_posts = get_top_posts(subset, req.sort_by, n=10)
            result.append({
                "topic": topic,
                "count": count,
                "pct":   pct,
                "posts": [serialize_row(row) for _, row in top_posts.iterrows()],
            })

        return {"status": "ok", "data": result, "total": total}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /api/topics/subtopics
# ─────────────────────────────────────────
@app.post("/api/topics/subtopics")
def get_subtopics(req: SubtopicsRequest):
    try:
        cache_key = f"{req.use_case}|{req.dataset}|{req.date_range}|{req.quarter_filter}|{normalize_stock_filter(req.stock_filter)}"

        if cache_key not in _topics_cache:
            raise HTTPException(status_code=400, detail="Run /api/topics first (cache miss)")

        df_classified = _topics_cache[cache_key].get("df_classified")
        if df_classified is None:
            raise HTTPException(status_code=400, detail="df_classified not in cache")

        df_topic = df_classified[df_classified["llm_topic"] == req.topic].copy()
        if len(df_topic) == 0:
            raise HTTPException(status_code=404, detail=f"No posts found for topic: {req.topic}")

        texts  = df_topic["full_text"].fillna("").tolist()
        n_sub  = min(5, max(3, len(texts) // 3))
        sample = "\n\n".join(
            [f"[DOC_{i}]: {t[:300]}" for i, t in enumerate(texts[:SAMPLE_SIZE])]
        )

        prompt = f"""You are analyzing Reddit posts about AI stocks.
These posts are specifically about the topic: "{req.topic}".

Example posts:
{sample}

Identify {n_sub} DISTINCT sub-topics within "{req.topic}".
Each sub-topic must be specific, appear in multiple posts, and meaningfully differ from the others.
Do NOT use the word "and". Keep sub-topic names short (2-5 words).

Return ONLY raw JSON array: ["Sub-topic 1", "Sub-topic 2", ...]"""

        response = get_deepseek().chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are a topic analyst. Return only a raw JSON array, no markdown."},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.3,
        )
        raw       = response.choices[0].message.content.strip()
        raw       = re.sub(r'^```(?:json)?\s*', '', raw)
        raw       = re.sub(r'\s*```$', '', raw)
        subtopics = json.loads(raw)
        if not isinstance(subtopics, list):
            subtopics = next(iter(subtopics.values()))

        sub_embeddings  = get_embeddings(subtopics)
        text_embeddings = get_embeddings(texts)
        sim_matrix      = cosine_similarity(text_embeddings, sub_embeddings)
        assigned        = [subtopics[int(np.argmax(row))] for row in sim_matrix]
        df_topic        = df_topic.copy()
        df_topic["subtopic"] = assigned

        tw = df_topic["subtopic"].value_counts().sort_values(ascending=True)
        sentiments      = ["bearish", "mixed", "neutral", "bullish"]
        topic_sentiment = pd.crosstab(df_topic["subtopic"], df_topic["sentiment"])
        topic_sentiment = topic_sentiment.reindex(
            index=tw.index,
            columns=[s for s in sentiments if s in topic_sentiment.columns],
            fill_value=0,
        )

        chart_data = {
            "topics": tw.index.tolist(),
            "counts": tw.values.tolist(),
            "sentiment_breakdown": {
                s: topic_sentiment[s].tolist() if s in topic_sentiment.columns
                else [0] * len(tw)
                for s in sentiments
            },
        }

        total  = len(df_topic)
        result = []
        for sub in tw.index.tolist()[::-1]:
            subset = df_topic[df_topic["subtopic"] == sub]
            count  = len(subset)
            pct    = round(count / total * 100, 1)
            top_posts = get_top_posts(subset, req.sort_by, n=10)
            result.append({
                "topic": sub,
                "count": count,
                "pct":   pct,
                "posts": [serialize_row(row) for _, row in top_posts.iterrows()],
            })

        topics_summary_data = [
            {"topic": r["topic"], "count": r["count"], "pct": r["pct"]} for r in result
        ]

        return {
            "status":       "ok",
            "chart_data":   chart_data,
            "posts_data":   result,
            "total":        total,
            "topics_data":  topics_summary_data,
            "parent_topic": req.topic,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /api/topics/emerging
# ─────────────────────────────────────────
@app.post("/api/topics/emerging")
def get_emerging_topics(req: EmergingRequest):
    try:
        df = load_and_filter(req.date_range, req.dataset, req.use_case)
        df = filter_by_stock(df, normalize_stock_filter(req.stock_filter))
        df = apply_quarter_filter(df, "")

        base_cache_key = f"{req.use_case}|{req.dataset}|{req.date_range}|{normalize_stock_filter(req.stock_filter)}|"
        if base_cache_key in _topics_cache:
            cached_df = _topics_cache[base_cache_key].get("df_classified")
            if cached_df is not None and len(cached_df) > 0:
                if "quarter_label" not in cached_df.columns:
                    cached_df = apply_quarter_filter(cached_df, "")
                df = cached_df

        available = sorted(df["quarter_label"].unique().tolist())
        if len(available) < 2:
            return {"status": "ok", "data": [], "curr": None, "prev1": None, "prev2": None,
                    "message": "Not enough quarters to compute emerging topics."}

        curr_q   = req.quarter_filter if req.quarter_filter in available else available[-1]
        curr_idx = available.index(curr_q)
        prev1_q  = available[curr_idx - 1] if curr_idx >= 1 else None
        prev2_q  = available[curr_idx - 2] if curr_idx >= 2 else None

        def quarter_shares(q):
            if q is None:
                return pd.Series(dtype=float)
            subset = df[df["quarter_label"] == q]
            if "llm_topic" not in subset.columns or len(subset) == 0:
                return pd.Series(dtype=float)
            return subset["llm_topic"].value_counts(normalize=True)

        curr_s  = quarter_shares(curr_q)
        prev1_s = quarter_shares(prev1_q)
        prev2_s = quarter_shares(prev2_q)

        all_topics = curr_s.index.union(prev1_s.index).union(prev2_s.index)
        curr_s  = curr_s.reindex(all_topics,  fill_value=0)
        prev1_s = prev1_s.reindex(all_topics, fill_value=0)
        prev2_s = prev2_s.reindex(all_topics, fill_value=0)

        change_vs_prev1 = curr_s - prev1_s
        change_vs_prev2 = curr_s - prev2_s

        emerging_mask = change_vs_prev1 > 0
        if prev2_q is not None:
            emerging_mask = emerging_mask & (change_vs_prev2 > 0)

        emerging = change_vs_prev1[emerging_mask].sort_values(ascending=False)

        def quarter_counts(q):
            if q is None:
                return pd.Series(dtype=int)
            subset = df[df["quarter_label"] == q]
            if "llm_topic" not in subset.columns or len(subset) == 0:
                return pd.Series(dtype=int)
            return subset["llm_topic"].value_counts()

        curr_c  = quarter_counts(curr_q).reindex(all_topics,  fill_value=0)
        prev1_c = quarter_counts(prev1_q).reindex(all_topics, fill_value=0)
        prev2_c = quarter_counts(prev2_q).reindex(all_topics, fill_value=0) if prev2_q else pd.Series(0, index=all_topics)

        result = []
        for topic in emerging.index:
            result.append({
                "topic":           topic,
                "curr_pct":        round(float(curr_s[topic])  * 100, 1),
                "prev1_pct":       round(float(prev1_s[topic]) * 100, 1),
                "prev2_pct":       round(float(prev2_s[topic]) * 100, 1) if prev2_q else None,
                "curr_count":      int(curr_c[topic]),
                "prev1_count":     int(prev1_c[topic]),
                "prev2_count":     int(prev2_c[topic]) if prev2_q else None,
                "change_vs_prev1": round(float(change_vs_prev1[topic]) * 100, 1),
                "change_vs_prev2": round(float(change_vs_prev2[topic]) * 100, 1) if prev2_q else None,
            })

        return {"status": "ok", "data": result, "curr": curr_q, "prev1": prev1_q, "prev2": prev2_q}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /api/unique-authors
# ─────────────────────────────────────────
@app.post("/api/unique-authors")
def get_unique_authors(req: UniqueAuthorsRequest):
    try:
        df = load_and_filter(req.date_range, req.dataset, req.use_case)
        df = filter_by_stock(df, normalize_stock_filter(req.stock_filter))

        def fmt_daily(d):     return d.strftime("%Y-%m-%d")
        def fmt_monthly(d):   return d.strftime("%Y-%b")
        def fmt_quarterly(d): return f"{d.year}Q{d.quarter}"
        def fmt_yearly(d):    return str(d.year)

        FREQ_MAP = {
            "daily":     ("D",  fmt_daily),
            "monthly":   ("ME", fmt_monthly),
            "quarterly": ("QE", fmt_quarterly),
            "yearly":    ("YE", fmt_yearly),
        }
        freq, fmt = FREQ_MAP.get(req.group_by, FREQ_MAP["quarterly"])

        stats = (
            df.set_index("date")
            .resample(freq)
            .agg(unique_authors=("author_handle", "nunique"))
            .reset_index()
        )
        stats["period_str"] = stats["date"].apply(fmt)

        result = stats[["period_str", "unique_authors"]].to_dict(orient="records")
        return {"status": "ok", "data": result}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /api/stock-chart
# ─────────────────────────────────────────
@app.post("/api/stock-chart")
def get_stock_chart(req: StockChartRequest):
    try:
        if not req.ticker:
            raise HTTPException(status_code=400, detail="Ticker is required.")

        # Map date range to yfinance period/start params
        ticker = req.ticker.strip().upper()

        if req.date_range.startswith("Custom:"):
            parts = req.date_range.split(":")
            start = parts[1] if len(parts) > 1 else None
            end   = parts[2] if len(parts) > 2 else None
            hist  = yf.Ticker(ticker).history(start=start, end=end)
        elif req.date_range == "Last 3 months":
            hist = yf.Ticker(ticker).history(period="3mo")
        elif req.date_range == "Last 6 months":
            hist = yf.Ticker(ticker).history(period="6mo")
        elif req.date_range == "Last 1 year":
            hist = yf.Ticker(ticker).history(period="1y")
        else:
            # All time — use from Jun 2025 to match dashboard data
            hist = yf.Ticker(ticker).history(start="2025-06-05")

        if hist.empty:
            raise HTTPException(status_code=404, detail=f"No data found for ticker: {ticker}. Check the ticker symbol.")

        hist.index = hist.index.tz_localize(None) if hist.index.tzinfo else hist.index

        result = [
            {
                "date":   str(row.Index.date()),
                "open":   round(float(row.Open),  2),
                "high":   round(float(row.High),  2),
                "low":    round(float(row.Low),   2),
                "close":  round(float(row.Close), 2),
                "volume": int(row.Volume),
            }
            for row in hist.itertuples()
        ]

        # Get company name
        info = yf.Ticker(ticker).info
        company_name = info.get("shortName", ticker)

        return {
            "status":       "ok",
            "ticker":       ticker,
            "company_name": company_name,
            "data":         result,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))