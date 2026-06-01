const API_BASE = "";

// ── Global state ──
let hasAnalyzed     = false;
let lastVolumeData  = [];
let lastUseCase     = "";
let lastDateRange   = "";
let lastTopicsData  = [];
let hasTopicsLoaded = false;

// ── Drilldown state ──
let _allTopicsList = [];
let _isDrilledDown = false;

// ── Store post texts for toggle ──
let _postTexts = [];

// ── Chart markers state ──
let _chartMarkers = [];  // [{date, title, sentiment}]
let _tradingDates = [];  // dates from loaded stock chart

// ── Sentiment config ──
const SENTIMENT_CONFIG = {
  bearish: { color: "#d62728", bg: "#fff5f5", label: "Bearish" },
  mixed:   { color: "#ff7f0e", bg: "#fff8f0", label: "Mixed"   },
  neutral: { color: "#1f77b4", bg: "#f0f6ff", label: "Neutral" },
  bullish: { color: "#2ca02c", bg: "#f0fff4", label: "Bullish" },
};

// ─────────────────────────────────────────
//  Helpers
// ─────────────────────────────────────────
function getUseCase() {
  return "AI Stocks";
}

function getDataset() {
  return "all";
}

function getStockFilter() {
  const sel = document.getElementById("stockTickerSelect");
  if (!sel) return "";
  if (sel.value === "custom") {
    const input = document.getElementById("stockFilterInput");
    return input ? input.value.trim() : "";
  }
  return sel.value;
}

function handleTickerSelectChange() {
  const sel = document.getElementById("stockTickerSelect");
  const customGroup = document.getElementById("customTickerGroup");
  if (!sel || !customGroup) return;
  customGroup.style.display = sel.value === "custom" ? "flex" : "none";
  if (sel.value !== "custom" && hasAnalyzed) handleAnalyze();
}

function getQuarterFilter() {
  const sel = document.getElementById("quarterSelect");
  return sel ? sel.value : "";
}

function getDateRange() {
  const sel = document.getElementById("dateSelect");
  if (!sel) return "All time";
  if (sel.value !== "Custom") return sel.value;
  const from = document.getElementById("customDateFrom").value;
  const to   = document.getElementById("customDateTo").value;
  if (!from || !to) return null;
  return `Custom:${from}:${to}`;
}

function getDateRangeLabel() {
  const sel = document.getElementById("dateSelect");
  if (!sel || sel.value !== "Custom") return sel ? sel.value : "All time";
  const from = document.getElementById("customDateFrom").value;
  const to   = document.getElementById("customDateTo").value;
  if (!from || !to) return "All time";
  return `${from} to ${to}`;
}

function handleDateSelectChange() {
  const val   = document.getElementById("dateSelect").value;
  const group = document.getElementById("customDateGroup");
  if (!group) return;
  group.style.display = val === "Custom" ? "flex" : "none";
  if (val !== "Custom" && hasAnalyzed) handleAnalyze();
}

function handleCustomDateChange() {}

// ─────────────────────────────────────────
//  Data Coverage
// ─────────────────────────────────────────
async function updateDataCoverage() {
  const dateRange = getDateRange();
  const dataset   = getDataset();
  const useCase   = getUseCase();
  try {
    const res  = await fetch(`${API_BASE}/api/coverage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date_range: dateRange, dataset, use_case: useCase })
    });
    const json = await res.json();
    const earliest = document.getElementById("dataEarliestDate");
    const latest   = document.getElementById("dataLatestDate");
    if (earliest) earliest.textContent = json.earliest_date || "—";
    if (latest)   latest.textContent   = json.latest_date   || "—";
  } catch (err) {
    console.warn("Could not fetch coverage:", err);
  }
}

// ─────────────────────────────────────────
//  Analyze button — fetches volume for top
//  charts, then kicks off topics
// ─────────────────────────────────────────
async function handleAnalyze(fromSort = false, fromGroupBy = false) {
  if ((fromSort || fromGroupBy) && !hasAnalyzed) return;

  const btn       = document.getElementById("analyzeBTN");
  const useCase   = getUseCase();
  const dateRange = getDateRange();
  const groupBy   = document.getElementById("groupBySelect").value;
  const dataset   = getDataset();

  if (dateRange === null) return;

  setLoading(btn, true);

  hasTopicsLoaded = false;
  _isDrilledDown  = false;

  try {
    // ── Volume data for top charts ──
    const volumeRes = await fetch(`${API_BASE}/api/volume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_case: useCase, date_range: dateRange, sort_by: "upvotes", dataset, group_by: groupBy, stock_filter: getStockFilter() })
    });
    if (!volumeRes.ok) throw new Error(`Volume error: ${volumeRes.status}`);
    const volumeJson = await volumeRes.json();
    if (volumeJson.status === "error") throw new Error(volumeJson.message);

    hasAnalyzed    = true;
    lastVolumeData = volumeJson.data;
    lastUseCase    = useCase;
    lastDateRange  = getDateRangeLabel();

    updateDataCoverage();

    document.getElementById("groupByWrapper").style.display = "flex";
    renderVolumeChart(volumeJson.data, groupBy);
    renderSentimentPie(volumeJson.data);

        // Replace the MOM block inside handleAnalyze with:
    try {
        const authorsRes = await fetch(`${API_BASE}/api/unique-authors`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ use_case: useCase, date_range: dateRange, dataset, group_by: groupBy, stock_filter: getStockFilter() })
        });
        const authorsData = await authorsRes.json();
        renderVolumeChart(volumeJson.data, groupBy, authorsData.data);
    } catch (e) {
        console.error("Unique authors fetch failed:", e);
        renderVolumeChart(volumeJson.data, groupBy, []);
    }

    // ── Now load topics ──
    if (!fromGroupBy) {
      await loadTopicsTab();
    }

  } catch (err) {
    console.error(err);
    showError("Could not connect to backend. Make sure api.py is running.");
  } finally {
    setLoading(btn, false);
  }
}

// ─────────────────────────────────────────
//  Top bar: Volume chart
// ─────────────────────────────────────────
function renderVolumeChart(data, groupBy = "quarterly", authorsData = []) {
  const GROUP_LABELS = { daily: "Day", monthly: "Month", quarterly: "Quarter", yearly: "Year" };
  const labels = data.map(d => d.period_str);

  const traces = [
    { x: labels, y: data.map(d => d.neg_count), name: "Negative", type: "bar", marker: { color: "#d62728" }, yaxis: "y" },
    { x: labels, y: data.map(d => d.mix_count), name: "Mixed",    type: "bar", marker: { color: "#ff7f0e" }, yaxis: "y" },
    { x: labels, y: data.map(d => d.neu_count), name: "Neutral",  type: "bar", marker: { color: "#1f77b4" }, yaxis: "y" },
    { x: labels, y: data.map(d => d.pos_count), name: "Positive", type: "bar", marker: { color: "#2ca02c" }, yaxis: "y" },
  ];

  const layout = {
    barmode:       "stack",
    xaxis:         { title: GROUP_LABELS[groupBy] || "Quarter", tickangle: -45, tickfont: { size: 9 }, showgrid: false },
    yaxis:         { tickfont: { size: 9 }, gridcolor: "#eee", title: "Posts" },
    showlegend:    false,
    height:        200,
    margin:        { t: 12, b: 60, l: 44, r: 16 },
    font:          { family: "DM Sans, sans-serif", size: 10 },
    plot_bgcolor:  "#f4f5f9",
    paper_bgcolor: "#ffffff",
  };

  if (authorsData && authorsData.length > 0) {
    const authorsLookup = {};
    authorsData.forEach(d => { authorsLookup[d.period_str] = d.unique_authors; });
    const authorValues = labels.map(l => authorsLookup[l] ?? null);

    const nonNull = authorValues.filter(v => v !== null);
    const minVal  = Math.min(...nonNull);
    const maxVal  = Math.max(...nonNull);
    const padding = (maxVal - minVal) * 0.3 || 1;

    traces.push({
      x:             labels,
      y:             authorValues,
      name:          "Unique Authors",
      type:          "scatter",
      mode:          "lines+markers",
      yaxis:         "y2",
      line:          { color: "#363737", width: 2, dash: "dot" },
      marker:        { color: "#363737", size: 6, symbol: "circle" },
      connectgaps:   false,
      hovertemplate: "%{x}: %{y} unique authors<extra></extra>",
    });

    layout.yaxis2 = {
      title:      "Unique Authors",
      overlaying: "y",
      side:       "right",
      tickfont:   { size: 9, color: "#363737" },
      titlefont:  { size: 9, color: "#363737" },
      gridcolor:  "rgba(0,0,0,0)",
      showgrid:   false,
      range:      [Math.max(0, minVal - padding), maxVal + padding],
    };

    layout.margin.r = 60;
  }

  document.getElementById("volumeChartPlaceholder")?.remove();
  Plotly.newPlot("volumeChartTop", traces, layout, { responsive: true })
    .then(() => Plotly.Plots.resize(document.getElementById("volumeChartTop")));
}

// ─────────────────────────────────────────
//  Top bar: Sentiment pie
// ─────────────────────────────────────────
function renderSentimentPie(data) {
  const totalNeg   = data.reduce((s, d) => s + d.neg_count, 0);
  const totalMix   = data.reduce((s, d) => s + d.mix_count, 0);
  const totalNeu   = data.reduce((s, d) => s + d.neu_count, 0);
  const totalPos   = data.reduce((s, d) => s + d.pos_count, 0);
  const grandTotal = totalNeg + totalMix + totalNeu + totalPos;

  const labels  = ["Positive", "Neutral", "Mixed", "Negative"];
  const values  = [totalPos, totalNeu, totalMix, totalNeg];
  const colors  = ["#6a9c2d", "#1f77b4", "#ff7f0e", "#d62728"];
  const pctText = values.map(v => grandTotal > 0 ? (v / grandTotal * 100).toFixed(1) + "%" : "0%");

  const trace = {
    labels:        labels.map((l, i) => `${l}  ${pctText[i]}`),
    values,
    type:          "pie",
    hole:          0.52,
    domain:        { x: [0.02, 0.48], y: [0.02, 0.98] },
    marker:        { colors, line: { color: "#fff", width: 2 } },
    textinfo:      "none",
    hovertemplate: "<b>%{label}</b><br>%{value} posts<extra></extra>",
    sort:          false,
  };
  const layout = {
    showlegend: true,
    legend:     { orientation: "v", x: 0.54, y: 0.5, font: { family: "DM Sans, sans-serif", size: 11 }, itemsizing: "constant" },
    height:     200,
    margin:     { t: 4, b: 4, l: 4, r: 4 },
    font:       { family: "DM Sans, sans-serif", size: 11 },
    paper_bgcolor: "#ffffff",
    annotations: [{
      text: `${grandTotal.toLocaleString()}<br>posts`,
      showarrow: false,
      font: { size: 12, family: "DM Sans, sans-serif", color: "#111827" },
      x: 0.245, y: 0.5, xanchor: "center", yanchor: "middle",
    }],
  };

  document.getElementById("sentimentPiePlaceholder")?.remove();
  Plotly.newPlot("sentimentPieTop", [trace], layout, { responsive: true })
    .then(() => Plotly.Plots.resize(document.getElementById("sentimentPieTop")));
}

 

// ─────────────────────────────────────────
//  Quarter selector
// ─────────────────────────────────────────
function populateQuarterSelector(quarters) {
  const sel = document.getElementById("quarterSelect");
  if (!sel) return;
  const currentVal = sel.value;
  sel.innerHTML = `<option value="">All quarters</option>`;
  (quarters || []).slice().reverse().forEach(q => {
    const opt = document.createElement("option");
    opt.value = q; opt.textContent = q;
    if (q === currentVal) opt.selected = true;
    sel.appendChild(opt);
  });
}

function updateQuarterPostCount(total) {
  const label = document.getElementById("quarterPostCount");
  if (!label) return;
  const q = getQuarterFilter();
  label.textContent = total > 0 ? `${total.toLocaleString()} posts & comments${q ? " in " + q : ""}` : "";
}

async function handleQuarterChange() {
  _isDrilledDown  = false;
  hasTopicsLoaded = false;

  const conv = document.getElementById("topicsGenaiConversation");
  if (conv) conv.innerHTML = "";
  document.getElementById("topicsGenaiPlaceholder").style.display  = "flex";
  document.getElementById("topicsGenaiConversation").style.display = "none";
  document.getElementById("topicsGenaiInputBar").style.display     = "none";

  const pc = document.getElementById("topicsPostsPlaceholder");
  if (pc) {
    pc.className = "posts-placeholder";
    pc.style.cssText = "";
    pc.innerHTML = `
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
      Posts will appear after topics load`;
  }

  const ep = document.getElementById("emergingPanel");
  if (ep) ep.style.display = "none";

  await loadTopicsTab();
}

// ─────────────────────────────────────────
//  Load Topics
// ─────────────────────────────────────────
async function loadTopicsTab() {
  const dataset       = getDataset();
  const dateRange     = getDateRange();
  const quarterFilter = getQuarterFilter();
  const useCase       = getUseCase();

  if (dateRange === null) return;

  _isDrilledDown = false;
  document.getElementById("drillSelectRow").style.display  = "flex";
  document.getElementById("drillBackBtn").style.display    = "none";
  document.getElementById("drillBreadcrumb").style.display = "none";
  document.getElementById("subtopicsChart").style.display  = "none";
  document.getElementById("topicsChart").style.display     = "none";

  const placeholder = document.getElementById("chartPlaceholder");
  placeholder.style.display = "flex";
  placeholder.innerHTML = `
    <svg class="spinner" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
    </svg>
    <span>Discovering topics${quarterFilter ? " for <strong style='color:#166534;'>" + quarterFilter + "</strong>" : ""}… this may take a moment</span>
  `;

  try {
    const res = await fetch(`${API_BASE}/api/topics`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset, date_range: dateRange, quarter_filter: quarterFilter, use_case: useCase, stock_filter: getStockFilter() })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (json.status === "error") throw new Error(json.message);

    const raw = json.data || {};
    const topicsData = {
      topics:              Array.isArray(raw.topics)                                              ? raw.topics              : [],
      counts:              Array.isArray(raw.counts)                                              ? raw.counts              : [],
      sentiment_breakdown: raw.sentiment_breakdown && typeof raw.sentiment_breakdown === "object" ? raw.sentiment_breakdown : {},
      quarters:            Array.isArray(raw.quarters)                                            ? raw.quarters            : [],
    };

    lastDateRange = getDateRangeLabel();
    populateQuarterSelector(topicsData.quarters);
    renderTopicsChart(topicsData);

    if (topicsData.topics.length > 0) {
      populateDrilldownSelector(topicsData.topics);
      if (getStockFilter()) {
        document.getElementById("emergingPanel").style.display = "none";
        await loadStockChart();
      } else {
        document.getElementById("stockChartPanel").style.display = "none";
        loadEmergingTopics();
      }

      const total = await loadTopicsPosts();
      updateQuarterPostCount(total);
      await callTopicsGenAI("");

      document.getElementById("topicsGenaiPlaceholder").style.display  = "none";
      document.getElementById("topicsGenaiConversation").style.display = "flex";
      document.getElementById("topicsGenaiInputBar").style.display     = "flex";
    } else {
      document.getElementById("topicsGenaiPlaceholder").style.display  = "flex";
      document.getElementById("topicsGenaiConversation").style.display = "none";
      document.getElementById("topicsGenaiInputBar").style.display     = "none";
      document.getElementById("drilldownPanel").style.display          = "none";
      updateQuarterPostCount(0);

      const pc = document.getElementById("topicsPostsPlaceholder");
      pc.className = "posts-placeholder"; pc.style.cssText = "";
      const sf = getStockFilter();
      pc.innerHTML = sf
        ? `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
           </svg>
           <span style="color:#6b7280;">No posts or comments mentioning <strong style="color:#166534;">${sf.toUpperCase()}</strong> were found.<br>Try a different ticker or remove the filter.</span>`
        : `<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
           </svg>
           No posts found for this quarter`;
    }

    hasTopicsLoaded = true;

  } catch (err) {
    console.error("[loadTopicsTab]", err);
    placeholder.style.display = "flex";
    placeholder.innerHTML = `<span style="color:#c8102e;">Failed to load topics: ${err.message}</span>`;
  }
}

// ─────────────────────────────────────────
//  Topics chart
// ─────────────────────────────────────────
function renderTopicsChart(data) {
  const placeholder = document.getElementById("chartPlaceholder");
  const el          = document.getElementById("topicsChart");

  if (!data || !Array.isArray(data.topics) || data.topics.length === 0) {
    el.style.display          = "none";
    placeholder.style.display = "flex";
    const q = getQuarterFilter();
    placeholder.innerHTML = `
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="1.5">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="8" x2="12" y2="12"/>
        <line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <span style="color:#6b7280;">
        No topics found${q ? " for <strong style='color:#166534;'>" + q + "</strong>" : ""}.
        Try a different quarter or date range.
      </span>`;
    return;
  }

  placeholder.style.display = "none";
  el.style.display          = "block";

  const sentimentColors = { negative: "#d62728", mixed: "#ff7f0e", neutral: "#1f77b4", positive: "#2ca02c" };
  const sb       = data.sentiment_breakdown || {};
  const truncate = (s, n = 40) => s.length > n ? s.slice(0, n) + "…" : s;
  const labels   = data.topics.map(t => truncate(t));
  const n        = labels.length;

  const traces = ["negative", "mixed", "neutral", "positive"].map(s => {
    const vals = Array.isArray(sb[s]) && sb[s].length === n ? sb[s] : Array(n).fill(0);
    return {
      x: vals, y: labels, orientation: "h", type: "bar",
      name:         s.charAt(0).toUpperCase() + s.slice(1),
      marker:       { color: sentimentColors[s] },
      text:         vals.map(v => v > 0 ? `${v}` : ""),
      textposition: "inside",
    };
  });

  const q = getQuarterFilter();
  Plotly.newPlot("topicsChart", traces, {
    barmode:       "stack",
    title:         `Topic Distribution (coloured by Sentiment)${q ? " · " + q : ""}`,
    xaxis:         { title: "Number of Posts", gridcolor: "#eee" },
    yaxis:         { gridcolor: "#eee", automargin: true },
    paper_bgcolor: "#ffffff",
    plot_bgcolor:  "#ffffff",
    font:          { family: "DM Sans, sans-serif", size: 12 },
    margin:        { t: 60, b: 60, l: 220, r: 20 },
    legend:        { title: { text: "Sentiment" }, orientation: "v", x: 1.02 },
    height:        420,
  }, { responsive: true });
}

// ─────────────────────────────────────────
//  Drilldown
// ─────────────────────────────────────────
function populateDrilldownSelector(topics) {
  _allTopicsList = topics;
  const sel = document.getElementById("drillTopicSelect");
  sel.innerHTML = `<option value="">— Choose a topic to drill into —</option>`;
  topics.slice().reverse().forEach(t => {
    const opt = document.createElement("option");
    opt.value = t; opt.textContent = t;
    sel.appendChild(opt);
  });
  document.getElementById("drilldownPanel").style.display = "flex";
}

async function handleDrilldown() {
  const topic         = document.getElementById("drillTopicSelect").value;
  const dataset       = getDataset();
  const dateRange     = getDateRange();
  const sortBy        = document.getElementById("topicsSortSelect").value;
  const quarterFilter = getQuarterFilter();
  const useCase       = getUseCase();

  if (!topic) { alert("Please choose a topic to drill into."); return; }

  const chartPlaceholder = document.getElementById("chartPlaceholder");
  chartPlaceholder.style.display = "flex";
  chartPlaceholder.innerHTML = `
    <svg class="spinner" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
    </svg>
    <span>Discovering sub-topics for <strong style="color:#166534;">${topic}</strong>${quarterFilter ? " · " + quarterFilter : ""}…</span>
  `;
  document.getElementById("topicsChart").style.display    = "none";
  document.getElementById("subtopicsChart").style.display = "none";

  const loadingDiv = addTopicsMessage(`Analyzing sub-topics within "${topic}"…`, "loading");

  const pc = document.getElementById("topicsPostsPlaceholder");
  pc.className = ""; pc.innerHTML = `<p style="color:#6b7280; padding:12px; font-size:12px;">Loading sub-topic posts…</p>`;
  pc.style.cssText = "display:block; padding:12px;";

  try {
    const res  = await fetch(`${API_BASE}/api/topics/subtopics`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset, date_range: dateRange, topic, sort_by: sortBy, quarter_filter: quarterFilter, use_case: useCase, stock_filter: getStockFilter() })
    });
    const json = await res.json();
    if (json.status === "error") throw new Error(json.message);

    chartPlaceholder.style.display = "none";
    renderSubtopicsChart(json.chart_data, topic);
    renderTopicsPosts(json.posts_data, json.total, dateRange, sortBy);
    updateQuarterPostCount(json.total);

    loadingDiv.remove();
    lastTopicsData = json.topics_data;
    await callTopicsGenAISubtopic(topic, json.topics_data, "");

    _isDrilledDown = true;
    document.getElementById("drillSelectRow").style.display     = "none";
    document.getElementById("drillBackBtn").style.display       = "flex";
    document.getElementById("drillBreadcrumb").style.display    = "block";
    document.getElementById("drillBreadcrumbTopic").textContent = topic;
    document.getElementById("emergingPanel").style.display      = "none";

  } catch (err) {
    chartPlaceholder.style.display = "flex";
    chartPlaceholder.innerHTML = `<span style="color:#c8102e;">Failed to load sub-topics: ${err.message}</span>`;
    loadingDiv.className = "genai-msg genai-msg--ai";
    loadingDiv.style.borderLeftColor = "#ef4444";
    loadingDiv.textContent = "Failed to generate sub-topic analysis.";
    console.error(err);
  }
}

function renderSubtopicsChart(data, parentTopic) {
  const el         = document.getElementById("subtopicsChart");
  const safeTopics = Array.isArray(data && data.topics) ? data.topics : [];
  const safeSB     = (data && typeof data.sentiment_breakdown === "object") ? data.sentiment_breakdown : {};

  if (safeTopics.length === 0) { el.style.display = "none"; return; }
  el.style.display = "block";

  const sentimentColors = { negative: "#d62728", mixed: "#ff7f0e", neutral: "#1f77b4", positive: "#2ca02c" };
  const truncate = (s, n = 38) => s.length > n ? s.slice(0, n) + "…" : s;
  const labels   = safeTopics.map(t => truncate(t));
  const n        = labels.length;

  const traces = ["negative", "mixed", "neutral", "positive"].map(s => {
    const vals = Array.isArray(safeSB[s]) && safeSB[s].length === n ? safeSB[s] : Array(n).fill(0);
    return {
      x: vals, y: labels, orientation: "h", type: "bar",
      name:         s.charAt(0).toUpperCase() + s.slice(1),
      marker:       { color: sentimentColors[s] },
      text:         vals.map(v => v > 0 ? `${v}` : ""),
      textposition: "inside",
    };
  });

  const q = getQuarterFilter();
  Plotly.newPlot("subtopicsChart", traces, {
    barmode:       "stack",
    title:         `Sub-topics: ${parentTopic}${q ? " · " + q : ""}`,
    xaxis:         { title: "Number of Posts", gridcolor: "#eee" },
    yaxis:         { gridcolor: "#eee", automargin: true },
    paper_bgcolor: "#ffffff",
    plot_bgcolor:  "#ffffff",
    font:          { family: "DM Sans, sans-serif", size: 12 },
    margin:        { t: 60, b: 60, l: 200, r: 20 },
    legend:        { title: { text: "Sentiment" }, orientation: "v", x: 1.02 },
  }, { responsive: true });
}

async function exitDrilldown() {
  _isDrilledDown = false;
  document.getElementById("drillSelectRow").style.display  = "flex";
  document.getElementById("drillBackBtn").style.display    = "none";
  document.getElementById("drillBreadcrumb").style.display = "none";
  document.getElementById("subtopicsChart").style.display  = "none";
  document.getElementById("topicsChart").style.display     = "block";

  if (getStockFilter()) {
    document.getElementById("emergingPanel").style.display = "none";
    await loadStockChart();
  } else {
    document.getElementById("stockChartPanel").style.display = "none";
    loadEmergingTopics();
  }

  const sortBy = document.getElementById("topicsSortSelect").value;
  const total  = await loadTopicsPosts(sortBy);
  updateQuarterPostCount(total);

  lastTopicsData = _allTopicsList.map(t => ({ topic: t, count: 0, pct: 0 }));
  await callTopicsGenAI("");
}

// ─────────────────────────────────────────
//  Emerging Topics
// ─────────────────────────────────────────
async function loadEmergingTopics() {
  const dataset       = getDataset();
  const dateRange     = getDateRange();
  const quarterFilter = getQuarterFilter();
  const useCase       = getUseCase();

  const panel = document.getElementById("emergingPanel");
  if (!panel) return;

  panel.style.display = "flex";
  document.getElementById("emergingContent").style.display   = "none";
  document.getElementById("emergingPeriodLabel").textContent = "";

  const placeholder = document.getElementById("emergingPlaceholder");
  placeholder.style.display = "flex";
  placeholder.innerHTML = `
    <svg class="spinner" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#16a34a" stroke-width="2.5">
      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
    </svg>
    <span style="color:#6b7280; font-size:12px;">Detecting emerging topics…</span>`;

  try {
    const res  = await fetch(`${API_BASE}/api/topics/emerging`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset, date_range: dateRange, quarter_filter: quarterFilter, use_case: useCase, stock_filter: getStockFilter() }),
    });
    const json = await res.json();
    if (json.status === "error") throw new Error(json.message);
    renderEmergingTopics(json);
  } catch (err) {
    console.error("[loadEmergingTopics]", err);
    placeholder.style.display = "flex";
    placeholder.innerHTML = `<span style="color:#c8102e; font-size:12px;">Failed to load emerging topics: ${err.message}</span>`;
  }
}

function renderEmergingTopics(json) {
  const placeholder = document.getElementById("emergingPlaceholder");
  const content     = document.getElementById("emergingContent");
  const periodLabel = document.getElementById("emergingPeriodLabel");
  const { data, curr, prev1, prev2 } = json;

  if (curr) periodLabel.textContent = [curr, prev1, prev2].filter(Boolean).join(" · ");

  if (!data || data.length === 0) {
    placeholder.style.display = "flex";
    placeholder.innerHTML = `
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="1.5">
        <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/>
      </svg>
      <span>No emerging topics detected for this period.</span>`;
    content.style.display = "none";
    return;
  }

  placeholder.style.display   = "none";
  content.style.display       = "flex";
  content.style.flexDirection = "column";
  content.innerHTML = `<div id="emergingChart" style="width:100%;"></div>`;

  const truncate = (s, n = 32) => s.length > n ? s.slice(0, n) + "…" : s;
  const labels   = data.map(t => truncate(t.topic));
  const colours  = { prev2: "#60a5fa", prev1: "#fb923c", curr: "#16a34a" };
  const quarters = [];
  if (prev2) quarters.push({ key: "prev2", label: prev2, color: colours.prev2, pctKey: "prev2_pct", cntKey: "prev2_count" });
  if (prev1) quarters.push({ key: "prev1", label: prev1, color: colours.prev1, pctKey: "prev1_pct", cntKey: "prev1_count" });
  quarters.push(            { key: "curr",  label: curr,  color: colours.curr,  pctKey: "curr_pct",  cntKey: "curr_count"  });

  const traces = quarters.map(q => {
    const pcts   = data.map(t => t[q.pctKey] ?? 0);
    const counts = data.map(t => t[q.cntKey] ?? 0);
    return {
      name: q.label, x: pcts, y: labels, orientation: "h", type: "bar",
      marker: { color: q.color },
      text:             counts.map((c, i) => c > 0 ? `${c} (${pcts[i]}%)` : ""),
      textposition:     "inside",
      insidetextanchor: "middle",
      textfont:         { size: 11, color: "#ffffff" },
      customdata:       counts,
      hovertemplate:    `<b>%{y}</b><br>${q.label}: %{x}% (%{customdata} posts)<extra></extra>`,
    };
  });

  Plotly.newPlot("emergingChart", traces, {
    barmode:       "group",
    title:         { text: "Emerging Topics — Share of Posts & Comments (%)", font: { size: 13, family: "DM Sans, sans-serif" } },
    xaxis:         { title: "Share (%)", gridcolor: "#e5e7eb", ticksuffix: "%", tickfont: { size: 12 } },
    yaxis:         { automargin: true, tickfont: { size: 12 } },
    paper_bgcolor: "#ffffff",
    plot_bgcolor:  "#ffffff",
    font:          { family: "DM Sans, sans-serif", size: 12 },
    margin:        { t: 48, b: 48, l: 10, r: 20 },
    legend:        { orientation: "h", y: -0.18, font: { size: 12 } },
    height:        Math.max(360, labels.length * 90 + 100),
    bargap:        0.25,
    bargroupgap:   0.08,
  }, { responsive: true});
}

// ─────────────────────────────────────────
//  Topics Posts
// ─────────────────────────────────────────
async function loadTopicsPosts(sortBy = "upvotes") {
  const dataset       = getDataset();
  const dateRange     = getDateRange();
  const quarterFilter = getQuarterFilter();
  const useCase       = getUseCase();

  const res  = await fetch(`${API_BASE}/api/topics/posts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset, date_range: dateRange, sort_by: sortBy, quarter_filter: quarterFilter, use_case: useCase, stock_filter: getStockFilter() })
  });
  const json = await res.json();
  if (json.status === "error") throw new Error(json.message);

  const safeData = Array.isArray(json.data) ? json.data : [];
  lastTopicsData = safeData.map(d => ({ topic: d.topic, count: d.count, pct: d.pct }));
  renderTopicsPosts(safeData, json.total != null ? json.total : 0, dateRange, sortBy);
  return json.total != null ? json.total : 0;
}

async function reloadTopicsPosts() {
  if (!hasTopicsLoaded || _isDrilledDown) return;
  const sortBy = document.getElementById("topicsSortSelect").value;
  const total  = await loadTopicsPosts(sortBy);
  updateQuarterPostCount(total);
}

function renderTopicsPosts(data, total, dateRange, sortBy) {
  const container = document.getElementById("topicsPostsPlaceholder");
  if (!data || data.length === 0) {
    container.innerHTML = `<p style="color:#888; padding:12px;">No posts found.</p>`;
    return;
  }

  const topicColors = ["#4e79a7","#f28e2b","#e15759","#76b7b2","#59a14f","#edc948","#b07aa1","#ff9da7","#9c755f","#bab0ac"];
  const q = getQuarterFilter();
  let html = `
    <div style="width:100%;">
      <p style="font-size:11px; color:#6b7280; margin-bottom:5px; padding:0 2px;">
        Top 10 posts & comments per topic sorted by <strong>${sortBy === "date" ? "most recent" : "most upvoted"}</strong>
        ${q ? `&nbsp;·&nbsp; <span style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:4px; padding:1px 6px; color:#166534; font-weight:600;">${q}</span>` : ""}
        ${getStockFilter() ? `&nbsp;·&nbsp; <span style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:4px; padding:1px 6px; color:#1d4ed8; font-weight:600;">📈 ${getStockFilter()}</span>` : ""}
      </p>`;

  data.forEach((t, ti) => {
    const color = topicColors[ti % topicColors.length];
    html += `
      <div style="margin-bottom:14px;">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:7px;">
          <span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:${color};"></span>
          <span style="font-size:11px; font-weight:700; color:${color}; text-transform:uppercase; letter-spacing:0.05em;">${t.topic}</span>
          <span style="font-size:11px; color:#9ca3af;">${t.count} posts · ${t.pct}%</span>
        </div>`;
    for (const p of t.posts) {
      const sc = SENTIMENT_CONFIG[p.sentiment] || { color: "#888", bg: "#f5f5f5", label: p.sentiment };
      html += buildPostCard(p, color, sc.color, sc.bg, sc.label);
    }
    html += `</div>`;
  });

  html += `</div>`;
  container.className     = "";
  container.innerHTML     = html;
  container.style.cssText = "display:block; padding:0 12px 12px 12px; overflow-y:auto; max-height:500px; width:100%;";
}

// ─────────────────────────────────────────
//  Post card builder
// ─────────────────────────────────────────
function buildPostCard(p, borderColor, badgeColor, badgeBg, badgeLabel) {
  const bodyPreview = p.full_text.length > 140 ? p.full_text.slice(0, 140) + "…" : p.full_text;
  const hasMore     = p.full_text.length > 140;
  const subreddit   = p.subreddit ? p.subreddit.replace("r/", "") : "";
  let toggleBtn     = "";

  if (hasMore) {
    const idx = _postTexts.length;
    _postTexts.push({ full: p.full_text, preview: bodyPreview });
    toggleBtn = `<span style="color:#555555; font-size:11px; cursor:pointer; margin-left:4px; font-weight:500;"
      onclick="togglePost(this, ${idx})">Show more</span>`;
  }

  // ── Source label ──
  const sourceLabel = p._source === "meltwater"
    ? `<span style="color:#0369a1;">${p.subreddit || "Meltwater"}</span>`
    : `<span style="color:#ff4500;">Reddit r/${subreddit}</span>`;

  // ── Meta line ──
  const metaLine = p._source === "meltwater"
    ? `${p.date} &nbsp;·&nbsp; ${p.content_type || "Article"}`
    : `${p.date} &nbsp;·&nbsp; 👍 ${p.upvotes} &nbsp;·&nbsp; 💬 ${p.comment_count}`;

  // ── Link label ──
  const linkLabel = p._source === "meltwater" ? "View article →" : "View on Reddit →";

  // ── Show title only for posts, not replies ──
  const isPost = !p.content_type ||
                 p.content_type === "Forum Post"  ||
                 p.content_type === "News Article" ||
                 p.content_type === "Social Post";

  return `
    <div style="margin-bottom:8px; padding:11px 13px; background:#fff; border-radius:7px; border:1px solid #e3e5ed; border-left:3px solid ${borderColor};">
      <div style="display:flex; align-items:center; gap:5px; margin-bottom:5px; font-size:11px; font-weight:600; color:#374151;">
        ${sourceLabel}
        <span style="color:#d1d5db;">·</span>
        <span style="color:#6b7280; font-weight:400;">${p.author || "unknown"}</span>
      </div>
      <div style="font-size:11px; color:#9ca3af; margin-bottom:6px;">
        ${metaLine}
      </div>
      ${p.title && isPost ? `<div style="font-size:13px; font-weight:700; color:#111827; margin-bottom:5px; line-height:1.4;">${p.title}</div>` : ""}
      <div style="font-size:12px; color:#4b5563; line-height:1.6;">
        <span class="post-preview">${bodyPreview}</span>
        ${toggleBtn}
      </div>
      <div style="margin-top:8px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:6px;">
        ${p.url ? `<a href="${p.url}" target="_blank" style="font-size:11px; color:#6b7280; text-decoration:none;">${linkLabel}</a>` : "<span></span>"}
        <div style="display:flex; align-items:center; gap:6px;">
          ${getStockFilter() ? (() => {
            const mIdx = _postTexts.length;
            _postTexts.push({ full: p.full_text, preview: bodyPreview, date: p.date, title: p.title || p.full_text.slice(0,60), sentiment: p.sentiment });
            return `<button onclick="toggleChartMarkerByIdx(this, ${mIdx})"
              style="display:inline-flex; align-items:center; gap:4px; font-size:10px; font-family:inherit; font-weight:600;
                     background:#f0fdf4; color:#166534; border:1px solid #bbf7d0; border-radius:12px;
                     padding:2px 8px; cursor:pointer; white-space:nowrap; transition:all 0.15s;">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
              Mark on chart
            </button>`;
          })() : ""}
          <span style="font-size:11px; font-weight:600; color:${badgeColor}; background:${badgeBg}; border:1px solid ${badgeColor}33; border-radius:20px; padding:2px 9px;">
            ${badgeLabel}
          </span>
        </div>
      </div>
    </div>`;
}

function togglePost(el, idx) {
  const preview = el.previousElementSibling;
  const data    = _postTexts[idx];
  if (el.textContent.trim() === "Show more") {
    preview.textContent = data.full;
    el.textContent = " Show less";
  } else {
    preview.textContent = data.preview;
    el.textContent = " Show more";
  }
}

// ─────────────────────────────────────────
//  GenAI — Topics
// ─────────────────────────────────────────
function addTopicsMessage(text, type) {
  const conv = document.getElementById("topicsGenaiConversation");
  const div  = document.createElement("div");
  div.className = `genai-msg genai-msg--${type}`;
  if (type === "ai") div.innerHTML = renderMarkdown(text);
  else div.textContent = text;
  conv.appendChild(div);
  conv.scrollTop = conv.scrollHeight;
  return div;
}

async function callTopicsGenAI(question) {
  if (question) addTopicsMessage(question, "user");
  const q          = getQuarterFilter();
  const useCase    = getUseCase();
  const loadingDiv = addTopicsMessage(
    question ? "Thinking..." : `Generating topics analysis${q ? " for " + q : ""}...`,
    "loading"
  );

  try {
    const res = await fetch(`${API_BASE}/api/genai`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        volume_data: [],
        use_case:    useCase,
        date_range:  q ? `${lastDateRange} · ${q}` : lastDateRange,
        question,
        mode:        "topics",
        topics_data: lastTopicsData,
      })
    });
    const json = await res.json();
    if (json.status === "error") throw new Error(json.message);

    loadingDiv.className = "genai-msg genai-msg--ai";
    loadingDiv.innerHTML = renderMarkdown(json.analysis);
    document.getElementById("topicsGenaiConversation").scrollTop = 999999;

  } catch (err) {
    loadingDiv.className = "genai-msg genai-msg--ai";
    loadingDiv.style.borderLeftColor = "#ef4444";
    loadingDiv.textContent = "Failed to generate analysis.";
  }
}

async function callTopicsGenAISubtopic(parentTopic, topicsData, question) {
  if (question) addTopicsMessage(question, "user");
  const q          = getQuarterFilter();
  const useCase    = getUseCase();
  const loadingDiv = addTopicsMessage(
    question ? "Thinking…" : `Generating sub-topic analysis for "${parentTopic}"${q ? " · " + q : ""}…`,
    "loading"
  );

  try {
    const summary    = topicsData.map(d => `  ${d.topic}: ${d.count} posts (${d.pct}%)`).join("\n");
    const qNote      = q ? `\nQuarter filter: ${q}` : "";
    const userPrompt = question
      ? `Sub-topic breakdown of "${parentTopic}" from Singapore Reddit posts about ${useCase}:${qNote}\n${summary}\n\nUser question: ${question}\n\nAnswer concisely.`
      : `Sub-topic breakdown of "${parentTopic}" from Singapore Reddit posts about ${useCase}:${qNote}\n${summary}\n\nWrite a short analytical summary (3-4 sentences) covering:\n1. The dominant sub-topics and what they reveal\n2. Any notable sentiment patterns across sub-topics\n3.`;

    const res  = await fetch(`${API_BASE}/api/genai`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ volume_data: [], use_case: useCase, date_range: lastDateRange, question: userPrompt, mode: "topics", topics_data: topicsData })
    });
    const json = await res.json();
    if (json.status === "error") throw new Error(json.message);

    loadingDiv.className = "genai-msg genai-msg--ai";
    loadingDiv.innerHTML = renderMarkdown(json.analysis);
    document.getElementById("topicsGenaiConversation").scrollTop = 999999;

  } catch (err) {
    loadingDiv.className = "genai-msg genai-msg--ai";
    loadingDiv.style.borderLeftColor = "#ef4444";
    loadingDiv.textContent = "Failed to generate sub-topic analysis.";
  }
}

async function askTopicsGenAI() {
  if (!hasTopicsLoaded) return;
  const input    = document.getElementById("topicsGenaiQuestion");
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  if (_isDrilledDown) {
    const parentTopic = document.getElementById("drillBreadcrumbTopic").textContent;
    await callTopicsGenAISubtopic(parentTopic, lastTopicsData, question);
  } else {
    await callTopicsGenAI(question);
  }
}

// ─────────────────────────────────────────
//  Stock Chart
// ─────────────────────────────────────────
async function loadStockChart() {
  const ticker    = getStockFilter().toUpperCase();
  const dateRange = getDateRange();
  const panel     = document.getElementById("stockChartPanel");
  const content   = document.getElementById("stockChartContent");
  const title     = document.getElementById("stockChartTitle");

  if (!panel) return;
  panel.style.display = "flex";
  content.innerHTML = `
    <div style="display:flex; align-items:center; justify-content:center; gap:8px; padding:20px; color:#6b7280; font-size:12px;">
      <svg class="spinner" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#166534" stroke-width="2.5">
        <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
      </svg>
      Loading ${ticker} chart…
    </div>`;

  try {
    const res  = await fetch(`${API_BASE}/api/stock-chart`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker, date_range: dateRange }),
    });
    const json = await res.json();
    if (json.status === "error") throw new Error(json.message);

    if (title) title.textContent = `${json.company_name} (${json.ticker})`;

    if (!json.data || json.data.length === 0) {
      content.innerHTML = `<p style="color:#6b7280; font-size:12px; padding:16px;">No price data found for <strong>${ticker}</strong>. Check the ticker symbol (e.g. NVDA, AMD, MSFT).</p>`;
      return;
    }

    const dates  = json.data.map(d => d.date);
    const opens  = json.data.map(d => d.open);
    const highs  = json.data.map(d => d.high);
    const lows   = json.data.map(d => d.low);
    const closes = json.data.map(d => d.close);

    const candlestick = {
      type:        "candlestick",
      x:           dates,
      open:        opens,
      high:        highs,
      low:         lows,
      close:       closes,
      increasing:  { line: { color: "#2ca02c" }, fillcolor: "#2ca02c" },
      decreasing:  { line: { color: "#d62728" }, fillcolor: "#d62728" },
      name:        json.ticker,
    };

    content.innerHTML = `<div id="stockCandlestick" style="width:100%;"></div>`;
  _chartMarkers = [];
  _tradingDates = dates;

    Plotly.newPlot("stockCandlestick", [candlestick], {
      title:         { text: "", font: { size: 12 } },
      xaxis:         { rangeslider: { visible: false }, tickfont: { size: 10 }, showgrid: false },
      yaxis:         { tickfont: { size: 10 }, gridcolor: "#eee", title: "Price (USD)" },
      paper_bgcolor: "#ffffff",
      plot_bgcolor:  "#ffffff",
      font:          { family: "DM Sans, sans-serif", size: 11 },
      margin:        { t: 12, b: 48, l: 56, r: 16 },
      height:        300,
      showlegend:    false,
    }, { responsive: true });

  } catch (err) {
    content.innerHTML = `<p style="color:#c8102e; font-size:12px; padding:16px;">Failed to load chart: ${err.message}</p>`;
  }
}

// ─────────────────────────────────────────
//  Chart Markers
// ─────────────────────────────────────────
function getNearestTradingDate(postDate) {
  if (!_tradingDates || _tradingDates.length === 0) return postDate;

  const post = new Date(postDate);
  let best = null;
  let bestDiff = Infinity;

  // First pass: find closest date on or after post date
  for (const d of _tradingDates) {
    const diff = new Date(d) - post;
    if (diff >= 0 && diff < bestDiff) {
      bestDiff = diff;
      best = d;
    }
  }

  // Fallback: find absolute closest date in either direction
  if (!best) {
    bestDiff = Infinity;
    for (const d of _tradingDates) {
      const diff = Math.abs(new Date(d) - post);
      if (diff < bestDiff) {
        bestDiff = diff;
        best = d;
      }
    }
  }

  return best || postDate;
}

function toggleChartMarkerByIdx(btn, idx) {
  const p = _postTexts[idx];
  if (!p) return;
  toggleChartMarker(btn, p.date, p.title, p.sentiment);
}

function toggleChartMarker(btn, date, title, sentiment) {
  const tradingDate = getNearestTradingDate(date);
  console.log(`[marker] post date: ${date} → trading date: ${tradingDate}, trading dates available: ${_tradingDates.length}`);
  const existing = _chartMarkers.findIndex(m => m.date === tradingDate && m.title === title);

  if (existing >= 0) {
    _chartMarkers.splice(existing, 1);
    btn.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg> Mark on chart`;
    btn.style.background = "#f0fdf4";
    btn.style.color = "#166534";
    btn.style.borderColor = "#bbf7d0";
  } else {
    const sc = SENTIMENT_CONFIG[sentiment] || { color: "#888" };
    const label = tradingDate !== date ? `${date} → ${tradingDate}` : date;
    _chartMarkers.push({ date: tradingDate, title: `[${label}] ${title}`, sentiment, color: sc.color });
    btn.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Remove marker`;
    btn.style.background = "#fff7ed";
    btn.style.color = "#c2410c";
    btn.style.borderColor = "#fed7aa";
  }

  updateChartMarkers();

  // Show/hide clear button
  const clearBtn = document.getElementById("clearMarkersBtn");
  if (clearBtn) clearBtn.style.display = _chartMarkers.length > 0 ? "inline-block" : "none";
}

function updateChartMarkers() {
  const chartDiv = document.getElementById("stockCandlestick");
  if (!chartDiv || !chartDiv.data) return;

  // Build Plotly shapes (vertical dashed lines)
  const shapes = _chartMarkers.map(m => ({
    type:      "line",
    x0:        m.date,
    x1:        m.date,
    y0:        0,
    y1:        1,
    xref:      "x",
    yref:      "paper",
    line:      { color: m.color, width: 1.5, dash: "dash" },
  }));

  // Build invisible scatter trace for hover tooltips
  const markerTrace = {
    type:        "scatter",
    mode:        "markers",
    x:           _chartMarkers.map(m => m.date),
    y:           _chartMarkers.map(() => null),  // auto y
    yaxis:       "y",
    marker:      { size: 10, color: _chartMarkers.map(m => m.color), opacity: 0 },
    text:        _chartMarkers.map(m => `${m.date}<br>${m.title.slice(0, 60)}…<br><em>${m.sentiment}</em>`),
    hoverinfo:   "text",
    showlegend:  false,
    name:        "markers",
  };

  // Replace last trace if it's our marker trace, else append
  const existingData = [...chartDiv.data];
  const lastTrace    = existingData[existingData.length - 1];
  if (lastTrace && lastTrace.name === "markers") {
    existingData[existingData.length - 1] = markerTrace;
  } else {
    existingData.push(markerTrace);
  }

  // Use Plotly.react for efficient update
  Plotly.react("stockCandlestick", existingData, {
    ...chartDiv.layout,
    shapes,
  });
}

function clearChartMarkers() {
  _chartMarkers = [];
  const clearBtn = document.getElementById("clearMarkersBtn");
  if (clearBtn) clearBtn.style.display = "none";
  const chartDiv = document.getElementById("stockCandlestick");
  if (chartDiv && chartDiv.layout) {
    Plotly.relayout("stockCandlestick", { shapes: [] });
  }
}

// ─────────────────────────────────────────
//  Shared utilities
// ─────────────────────────────────────────
function renderMarkdown(text) {
  return text
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n\n/g, "<br><br>")
    .replace(/\n- /g, "<br>• ")
    .replace(/^- /gm, "• ")
    .replace(/\n/g, "<br>");
}

function setLoading(btn, isLoading) {
  if (isLoading) {
    btn.classList.add("loading");
    btn.innerHTML = `
      <svg class="spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
      </svg>
      Analyzing...`;
  } else {
    btn.classList.remove("loading");
    btn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
      </svg>
      Analyze`;
  }
}

function showError(message) {
  const placeholder = document.getElementById("chartPlaceholder");
  placeholder.style.display = "flex";
  placeholder.innerHTML = `
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#c8102e" stroke-width="1.5">
      <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
    </svg>
    <span style="color:#c8102e; font-weight:500;">${message}</span>`;
}