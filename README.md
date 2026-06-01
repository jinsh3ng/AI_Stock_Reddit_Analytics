# AI Stock Reddit Analytics Dashboard

> **Note:** This repository is shared as a code reference only. The database, API keys, and scraped data are private and not included — the project cannot be run directly without setting up your own data infrastructure. Feel free to use the code structure and implementation as a reference for building something similar.

> **⚠️ The live demo is no longer maintained** as it incurs ongoing costs across multiple services — Railway hosting, AWS EC2, and rotating proxy subscriptions. This project is intended to demonstrate what I am capable of building. A video demonstration is available below.

<p align="center">
  <img width="1890" height="930" alt="image" src="https://github.com/user-attachments/assets/6aca9c1d-36b1-437f-ac76-4b364474887f" />
</p>

<p align="center">
  <a href="https://drive.google.com/file/d/1-0RK0JnayIR3x_GgEBIya5ZQMXgq3_-Z/view?usp=sharing">▶ Watch Video Demonstration</a>
</p>

If you have any questions or see anything that can be improved, feel free to reach out:

- **Name:** Chong Jinsheng
- **LinkedIn:** [www.linkedin.com/in/jinsh3ng](https://www.linkedin.com/in/jinsh3ng)
- **Email:** Jinsh3ng@hotmail.com
---

## 1. Overview

This project monitors Reddit discussions about AI-related stocks in real time. It scrapes posts and comments, classifies them by sentiment (bullish / bearish / neutral / mixed), discovers trending topics using LLM-powered analysis, and presents everything in an interactive dashboard.

This project consists of:

- **Frontend:** Built using HTML, CSS, and JavaScript with Plotly.js for interactive charts
- **Backend:** Built using FastAPI, which exposes API endpoints for topic analysis, sentiment breakdowns, volume charts, and GenAI summaries
- **Scraping Pipeline:** Collects and processes Reddit data on a weekly schedule

The dashboard is deployed on Railway via Docker. The scraping pipeline runs on AWS EC2, automated weekly via a cron job.

Data is filtered to show posts from **1 May 2025 onwards**. Earlier data was collected but excluded after EDA revealed inconsistent scraping coverage prior to this date.

### Dashboard Features

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

## 2. System Architecture

<p align="center">
  <img width="1253" height="580" alt="image" src="https://github.com/user-attachments/assets/7b338f3e-433a-44e0-ad6c-2245585d9ea9" />
</p>

---

## 3. Repository Structure

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

## 4. Environment Variables

Configure the `.env` file with the following credentials:

| Variable | Required For | Notes |
|---|---|---|
| `DEEPSEEK_API_KEY` | Dashboard + scraping | Required for topic classification and sentiment analysis |
| `DATABASE_URL` | Dashboard | PostgreSQL connection string |
| `PROXY_SERVER` | Scraping pipeline | Rotating proxy server |
| `PROXY_USERNAME` | Scraping pipeline | Proxy credentials |
| `PROXY_PASSWORD` | Scraping pipeline | Proxy credentials |
| `AI_STOCKS_QUERIES` | Scraping pipeline | Comma-separated Reddit search queries |

---

## 5. Cloud Deployment

### Dashboard (Railway + Docker)

The dashboard is containerised with Docker and deployed on Railway. The pre-built image is available on Docker Hub — you do not need to build it yourself.

**1. Set up PostgreSQL on Railway:**
- New Project → Add PostgreSQL plugin
- Railway will provision a database and provide a `DATABASE_PUBLIC_URL` — use this as your `DATABASE_URL` environment variable

**2. Deploy the dashboard:**
- New Project → Deploy from Docker image → enter `jinsh3ng/ai-stocks-dashboard:latest`
- Go to Variables tab → add `DATABASE_URL` and `DEEPSEEK_API_KEY`
- Go to Settings → Networking → Generate Domain → set port to `8000`

**3. Populate the database:**
- Once PostgreSQL and the dashboard are running, set up the scraping pipeline on EC2 (see below) and run it at least once to populate the database
- The dashboard will show data after the first successful pipeline run

### Scraping Pipeline (AWS EC2)

The scraping pipeline runs on AWS EC2 rather than Railway because a full run takes several hours — Railway enforces short execution limits.

**1. Launch an EC2 instance:**
- AMI: Ubuntu 22.04 LTS
- Instance type: `t3.small` (2 vCPU, 2GB RAM)
- Storage: 20GB
- Open port 22 (SSH) in the security group

**2. Set up the environment:**
```bash
ssh -i "your-key.pem" ubuntu@your-ec2-ip
sudo apt update && sudo apt install -y python3-pip python3-venv libxml2-dev libxslt1-dev
mkdir ~/ai-stocks && cd ~/ai-stocks
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium && playwright install-deps chromium
```

**3. Copy scripts and configure `.env`:**
```bash
# From your local machine
scp -i "your-key.pem" -r ./scraping_scripts/ ubuntu@your-ec2-ip:~/ai-stocks/
scp -i "your-key.pem" .env ubuntu@your-ec2-ip:~/ai-stocks/
```

**4. Run the pipeline:**
```bash
source ~/ai-stocks/venv/bin/activate
nohup ~/ai-stocks/venv/bin/python -u run_pipeline.py >> ~/ai-stocks/pipeline.log 2>&1 &
tail -f ~/ai-stocks/pipeline.log
```

**5. Create a startup script:**
```bash
nano ~/ai-stocks/startup.sh
```

Paste the following into the file:
```bash
#!/bin/bash
sleep 30
cd /home/ubuntu/ai-stocks
source venv/bin/activate
python -u run_pipeline.py >> /home/ubuntu/ai-stocks/pipeline.log 2>&1
sudo shutdown -h now
```

Make it executable:
```bash
chmod +x ~/ai-stocks/startup.sh
```

**6. Configure it to run on every boot:**
```bash
crontab -e
# Add this line
@reboot /home/ubuntu/ai-stocks/startup.sh
```

The instance boots up, waits 60 seconds to fully initialise, runs the pipeline, then shuts itself down automatically — so it only incurs cost during the scraping window.

**7. Schedule weekly via EventBridge + Lambda:**
 
Use AWS EventBridge to trigger a Lambda function every Monday that starts the EC2 instance. The startup script then handles the rest automatically.
 
Lambda function (Python runtime, requires `ec2:StartInstances` IAM permission):
```python
import boto3
 
def lambda_handler(event, context):
    ec2 = boto3.client('ec2', region_name='your-region')
    ec2.start_instances(InstanceIds=['your-instance-id'])
    return {'statusCode': 200, 'body': 'EC2 started'}
```
 
EventBridge cron expression (every Sunday at 12am SGT):
```
cron(0 16 ? * SUN *)
```
 
> The exact setup involves a few steps across Lambda, IAM, and EventBridge — some experimentation may be needed depending on your AWS configuration.
 
---

## 6. Algorithms

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

## 7. Scraping Pipeline

The pipeline collects Reddit posts and comments about AI stocks, processes the content, filters irrelevant posts, and stores the results in PostgreSQL.

| Step | Script | Description |
|---|---|---|
| 1 | `step1_scrape_urls.py` | Crawl Reddit search results for each keyword query, extract post URLs |
| 2 | `step2_extract_content.py` | Fetch full post + comment content by appending `.json` to each Reddit post URL |
| 3 | `step3_update_dates.py` | Backfill post dates from raw data into the URL index table |
| 4 | `step4_classify.py` | BM25 pre-filter → DeepSeek relevance + sentiment classification → store in cleaned table |

### Step 1 — Extract Reddit URLs

Reddit search result pages are crawled for each query in `AI_STOCKS_QUERIES`. Post URLs, titles, and subreddits are extracted and stored in `reddit_posts_urls`. This table acts as the master index of discovered posts.

### Step 2 — Extract Content

Full post and comment content is fetched by appending `.json` to each Reddit post URL, which returns the raw post data in JSON format. Rotating proxies are used to reduce the likelihood of rate limiting. Output is stored in `raw_data`.

### Step 3 — Incremental Updates

The URL table is updated with the post date after content extraction. On subsequent runs, posts older than 6 months are skipped (already fully scraped) and only newer posts are re-scraped to capture new comments.

### Step 4 — Filter & Classify

Raw posts are first scored by BM25 against the keyword query list to remove obviously irrelevant content. The remaining posts are sent to DeepSeek for relevance and sentiment classification, then stored in `cleaned_data_ai_stocks`.

---

## 8. Further Improvements

### Additional Data Sources

Currently the project scrapes Reddit only. Other platforms such as X (Twitter), StockTwits, or financial news sites could be integrated to broaden coverage and reduce platform-specific bias.

### Classification Accuracy

The current DeepSeek classification performs well but could be further validated against a manually labelled ground truth dataset for more rigorous accuracy measurement across relevance, sentiment, and topic assignment.
