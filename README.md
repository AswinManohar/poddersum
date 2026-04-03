# Podcast Summarizer Agentic System

This project is a podcast summarizer that uses **LangGraph** for an agentic workflow and **Gemini 3.0 Flash** for native audio summarization.

## Features
- **Agentic Workflow:** Uses LangGraph to manage the download, summarization, and interactive chat phases.
- **Persistent Memory:** Uses `SqliteSaver` to remember conversation history and summary state across sessions.
- **Interactive Front-end:** A Streamlit app to browse podcasts, generate summaries, and chat with the agent.
- **Scheduled Summarization:** A `main.py` script for daily cron jobs to fetch the latest episodes in the current month.

## Summarized Podcasts
- 10% Happier with Dan Harris
- Hidden Brain
- Sean Carroll's Mindscape: Science, Society, Philosophy, Culture, Arts, and Ideas

## Prerequisites
- Python 3.10+
- gPodder installed and configured.
- Gemini API Key.

## Installation
```bash
pip install langgraph langgraph-checkpoint-sqlite streamlit google-genai requests langchain-core langsmith python-dotenv feedparser
```

## Usage

### 1. Interactive Front-end
Run the Streamlit app to browse and chat:
```bash
streamlit run app.py
```

### 2. Scheduled Summarization
Set up a cron job to run the script using the background runner:
```bash
(crontab -l 2>/dev/null; echo "@reboot /home/aswinmanohar/poddersum/run_agent.sh") | crontab -
(crontab -l 2>/dev/null; echo "0 */4 * * * /home/aswinmanohar/poddersum/run_agent.sh") | crontab -
```

## How it Works
1. **Fetch:** Reads the gPodder SQLite database directly.
2. **Download:** Downloads the latest unplayed episode for each podcast (only from the current month).
3. **Summarize:** Uploads audio to Gemini 3 Flash, which generates a rich Markdown summary.
4. **Chat:** The agent enters a "Waiting" state (interrupt) in the LangGraph. You can resume it in the UI to ask follow-up questions.
5. **Persistence:** Everything is saved in `checkpoints.db`.
6. **Cost Tracking:** Token usage and estimated costs are tracked and displayed in the dashboard.
