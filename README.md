# AI Stock Reddit Analytics Dashboard

> **Note:** This repository is shared as a code reference only. The database, API keys, and scraped data are private and not included — the project cannot be run directly without setting up your own data infrastructure. Feel free to use the code structure and implementation as a reference for building something similar.

If you have any questions, feel free to contact me:

- **Name:** Chong Jinsheng
- **LinkedIn:** www.linkedin.com/in/jinsh3ng
- **Email:** Jinsh3ng@hotmail.com

---

## 1. Overview

This project monitors Reddit discussions about AI-related stocks in real time. It scrapes posts and comments, classifies them by sentiment (bullish / bearish / neutral / mixed), discovers trending topics using LLM-powered analysis, and presents everything in an interactive dashboard.

This project consists of:

- **Frontend:** Built using HTML, CSS, and JavaScript with Plotly.js for interactive charts
- **Backend:** Built using FastAPI, which exposes API endpoints for topic analysis, sentiment breakdowns, volume charts, and GenAI summaries
- **Scraping Pipeline:** Collects and processes Reddit data on a weekly schedule

The dashboard is deployed on Railway via Docker. The scraping pipeline runs on AWS EC2, automated weekly via a cron job.

---

## 2. Repository Structure

```plaintext
AI-Stock-Reddit-Analytics/
├── Dashboard/
│   ├── backend/
│   │   └── api.py                          # FastAPI backend — all endpoints
│   │
│   ├── frontend/
│   │   ├── app.js                          # Dashboard logic and chart rendering
│   │   ├── index.html                      # Main dashboard page
│   │   ├── styles.css                      # Dashboard styling
│   │   └── reddit_logo.png
│   │
│   ├── Dockerfile                          # Docker build configuration
│   ├── Procfile                            # Railway deployment configuration
│   └── requirements.txt                   # Dashboard dependencies
│
├── scraping_scripts/
│   ├── parser.py                           # Reddit JSON parser
│   ├── run_pipeline.py                     # Orchestrates all 4 scraping steps
│   ├── step1_scrape_urls.py               # Extract Reddit post URLs from search results
│   ├── step2_extract_content.py           # Retrieve and parse full post content
│   ├── step3_update_dates.py              # Backfill post dates in database
│   └── step4_classify.py                  # BM25 filter + DeepSeek classification
│
├── EDA/                                    # Exploratory data analysis notebooks
│
├── .env                                    # Environment variables (not included)
└── README.md
```

---

## 3. Environment Variables

Configure the `.env` file with the following credentials:

| Variable | Required For | Notes |
|---|---|---|
| `DEEPSEEK_API_KEY` | Dashboard + scraping | Required for topic classification and sentiment analysis |
| `DATABASE_URL` | Dashboard | PostgreSQL connection string |
| `PROXY_SERVER` | Scraping pipeline | Rotating proxy server (e.g. Oxylabs) |
| `PROXY_USERNAME` | Scraping pipeline | Proxy credentials |
| `PROXY_PASSWORD` | Scraping pipeline | Proxy credentials |
| `AI_STOCKS_QUERIES` | Scraping pipeline | Comma-separated Reddit search queries |

---

## 4. Pipeline Architecture

The data flows through four stages from raw Reddit posts to dashboard-ready analytics:

```
Step 1 — Scrape URLs
  Reddit search queries → reddit_posts_urls table

Step 2 — Extract content
  Post URLs → full post + comment content → raw_data table

Step 3 — Update dates
  Backfill post dates from raw_data into reddit_posts_urls

Step 4 — Classify
  BM25 relevance filter → DeepSeek sentiment classification → cleaned_data_ai_stocks table

Dashboard
  FastAPI reads cleaned_data_ai_stocks → serves analytics to frontend
```

---

## 5. Algorithms

### Large Language Models

DeepSeek AI is used across three main components:

1. **Topic discovery** — identifies key themes from a sample of posts
2. **Sentiment classification** — labels each post as bullish, bearish, neutral, or mixed
3. **GenAI analysis** — generates natural language summaries of topic breakdowns on the dashboard

### Topic Modelling

After experimenting with multiple approaches, LLMs were most effective for topic identification. However, classifying every post individually would be too slow and costly, so a hybrid approach is used:

1. **Sample & Discover** — randomly sample 100 posts and send them to DeepSeek to identify key themes
2. **Embed** — convert all posts into numerical vectors using `sentence-transformers` (`all-MiniLM-L6-v2`)
3. **Classify via Cosine Similarity** — assign each post to its most similar topic based on embedding distance

This reduces LLM calls from thousands to one, significantly cutting cost and latency while maintaining analytical quality.

#### Previous Approaches Considered

| Approach | Issue |
|---|---|
| **BERTopic / LDA** | Keyword-dependent; generated topics lacked sufficient context and interpretability |
| **Full LLM classification** | Too slow and expensive at scale — hence the sampling strategy above |

### Emerging Topics

After topic classification, emerging topics are identified by comparing topic share across quarters. A topic is flagged as **emerging** if its proportion in the current quarter exceeds both the previous quarter and the quarter before that.

### Stock Filter (BM25)

When a user filters by a specific ticker (e.g. NVDA), BM25 relevance scoring is applied against the post corpus. Only posts with a score above zero are returned — posts where the ticker or company name appears at least once. This is fast and requires no additional embeddings at query time.

---

## 6. Scraping Pipeline

The pipeline collects Reddit posts and comments about AI stocks, processes the content, filters irrelevant posts, and stores the results in PostgreSQL.

| Step | Script | Description |
|---|---|---|
| 1 | `step1_scrape_urls.py` | Crawl Reddit search results for each keyword query, extract post URLs |
| 2 | `step2_extract_content.py` | Fetch full post + comment content via Reddit's JSON API |
| 3 | `step3_update_dates.py` | Backfill post dates from raw data into the URL index table |
| 4 | `step4_classify.py` | BM25 pre-filter → DeepSeek relevance + sentiment classification → store in cleaned table |

### Step 1 — Extract Reddit URLs

Reddit search result pages are crawled for each query in `AI_STOCKS_QUERIES`. Post URLs, titles, and subreddits are extracted and stored in `reddit_posts_urls`. This table acts as the master index of discovered posts.

### Step 2 — Extract Content

Full post and comment content is fetched via Reddit's `.json` endpoint for each URL. Rotating proxies are used to reduce the likelihood of rate limiting. Output is stored in `raw_data`.

### Step 3 — Incremental Updates

The URL table is updated with the post date after content extraction. On subsequent runs, posts older than 6 months are skipped (already fully scraped) and only newer posts are re-scraped to capture new comments.

### Step 4 — Filter & Classify

Raw posts are first scored by BM25 against the keyword query list to remove obviously irrelevant content. The remaining posts are sent to DeepSeek for relevance and sentiment classification, then stored in `cleaned_data_ai_stocks`.

---

## 7. Cloud Deployment

### Dashboard (Railway + Docker)

The dashboard is containerised with Docker and deployed on Railway.

```bash
# Build image
docker build -t ai-stocks-dashboard .

# Push to Docker Hub
docker tag ai-stocks-dashboard yourusername/ai-stocks-dashboard:latest
docker push yourusername/ai-stocks-dashboard:latest
```

On Railway: New Project → Deploy from Docker image → enter your Docker Hub image name → add environment variables in the Variables tab → generate a public domain under Settings → Networking.

### Scraping Pipeline (AWS EC2)

The scraping pipeline is deployed on AWS EC2 rather than Railway because a full run takes several hours — Railway enforces short execution limits.

The pipeline runs on a `t3.small` Ubuntu 22.04 instance, triggered weekly via a cron job:

```bash
# SSH in and activate venv
ssh -i "your-key.pem" ubuntu@your-ec2-ip
source ~/ai-stocks/venv/bin/activate

# Run manually
nohup ~/ai-stocks/venv/bin/python -u run_pipeline.py >> ~/ai-stocks/pipeline.log 2>&1 &

# Watch logs
tail -f ~/ai-stocks/pipeline.log
```

Weekly cron (every Monday 9am UTC):
```
0 9 * * 1 /home/ubuntu/ai-stocks/venv/bin/python -u /home/ubuntu/ai-stocks/run_pipeline.py >> /home/ubuntu/ai-stocks/pipeline.log 2>&1
```

---

## 8. Dashboard Features

- **Volume analytics** — post and comment volume over time, grouped by day / month / quarter / year
- **Sentiment breakdown** — bullish / bearish / neutral / mixed distribution as stacked bar and pie chart
- **Unique authors** — overlaid line chart showing distinct poster count per period
- **Topic analysis** — LLM-discovered topics coloured by sentiment, with drill-down into sub-topics
- **Emerging topics** — topics gaining share quarter-over-quarter
- **Stock price chart** — candlestick chart via yfinance, shown when a ticker filter is active
- **Post markers** — mark individual posts directly onto the candlestick chart to correlate sentiment with price movement
- **GenAI analysis** — DeepSeek summarises the topic breakdown and answers follow-up questions
- **Stock ticker filter** — filter all posts, topics, and charts to a specific ticker (preset list + custom input)

---

## 9. Further Improvements

### Additional Data Sources

Currently the project scrapes Reddit only. Other platforms such as X (Twitter), StockTwits, or financial news sites could be integrated to broaden coverage and reduce platform-specific bias.

### Sentiment Accuracy

The current DeepSeek classification performs well but could be further validated against a manually labelled ground truth dataset for more rigorous accuracy measurement.

### Real-time Scraping

The current pipeline runs weekly. Moving to near-real-time scraping (hourly or daily) would make the dashboard more responsive to fast-moving market events.

### Price Correlation Analysis

The post marker feature on the candlestick chart is a manual tool. An automated correlation analysis between post sentiment spikes and price movements would be a natural next step.