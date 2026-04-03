# Podcast Summarizer Agentic System

This project is a podcast summarizer that uses **LangGraph** for an agentic workflow and **Gemini 2.0 Flash** for native audio summarization.

## Features
- **Agentic Workflow:** Uses LangGraph to manage the download, summarization, and interactive chat phases.
- **Persistent Memory:** Uses `SqliteSaver` to remember conversation history and summary state across sessions.
- **Interactive Front-end:** A Streamlit app to browse podcasts, generate summaries, and chat with the agent.
- **Scheduled Summarization:** A `main.py` script for daily cron jobs to fetch the latest episodes in the current month.

## Prerequisites
- Python 3.10+
- gPodder installed and configured.
- Gemini API Key.

## Installation
```bash
pip install langgraph langgraph-checkpoint-sqlite streamlit google-genai requests langchain-core
```

## Usage

### 1. Interactive Front-end
Run the Streamlit app to browse and chat:
```bash
export GEMINI_API_KEY="your_api_key"
streamlit run app.py
```

### 2. Scheduled Summarization
Set up a cron job to run the script daily:
```bash
0 3 * * * export GEMINI_API_KEY="your_api_key" && /usr/bin/python3 /home/aswinmanohar/poddersum/main.py >> /home/aswinmanohar/poddersum/cron.log 2>&1
```

## How it Works
1. **Fetch:** Reads the gPodder SQLite database directly.
2. **Download:** Downloads the latest unplayed episode for each podcast (only from the current month).
3. **Summarize:** Uploads audio to Gemini 3 Flash, which generates a rich Markdown summary.
4. **Chat:** The agent enters a "Waiting" state (interrupt) in the LangGraph. You can resume it in the UI to ask follow-up questions.
5. **Persistence:** Everything is saved in `checkpoints.db`.
