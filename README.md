# 🎙️ PodderSum: Agentic Podcast Summarizer
 
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/Framework-LangGraph-orange)](https://github.com/langchain-ai/langgraph)
[![AI Model](https://img.shields.io/badge/Model-Gemini%203.1%20Flash-green)](https://aistudio.google.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
 
**PodderSum** is an intelligent, agentic podcast assistant. It automates the "listen, summarize, and discuss" loop using **LangGraph** for workflow orchestration and **Gemini 3.1 Flash** for native audio intelligence.
 
---
 
## Features
 
- **Agentic Workflow:** Uses LangGraph to manage state across downloading, summarization, and human-in-the-loop chat phases.
- **Native Audio Processing:** Gemini 3.1 Flash "listens" to audio directly—no separate transcription step required for summarization.
- **Diarized Transcripts:** Generates high-quality verbatim transcripts with speaker identification.
- **Persistent Memory:** Integrated `SqliteSaver` remembers your conversation history and episode states across sessions.
- **Cost-Efficient:** Optimized for Gemini 3.1 Flash-Lite to keep bulk processing costs near zero.
- **Interactive Dashboard:** Built with Streamlit for a seamless browsing and chatting experience.
 
---
 
## 🛠️ Prerequisites
 
- **Python 3.10+**
- **gPodder:** Installed and initialized ([Download here](https://gpodder.github.io/))
- **Google Gemini API Key:** ([Get one at Google AI Studio](https://aistudio.google.com/))
 
---
 
## 🚀 Quick Start (Linux & Mac)
 
### 1. Install gPodder
 
PodderSum relies on gPodder's local SQLite database to track your subscriptions.
 
- **Mac:** Drag the app to `/Applications`.
- **Linux:** `sudo apt install gpodder`
- **Crucial:** Open gPodder once to initialize your database.
 
### 2. Setup the Environment
 
Clone this repository and install the dependencies:
 
```bash
pip install streamlit google-genai langgraph langgraph-checkpoint-sqlite feedparser python-dotenv requests langchain-core
```
 
Create a `.env` file in the root directory:
 
```
GEMINI_API_KEY=your_actual_key_here
```
 
### 3. Launch the App
 
```bash
streamlit run app.py
```
 
---
 
## Adding Podcasts
 
| Method | Instructions |
|--------|-------------|
| **In-App** | Use the "Subscribe" section in the Streamlit sidebar with an RSS URL. |
| **gPodder** | Add via `Podcasts -> Subscribe to new podcast via URL` in the desktop app. |
 
---
 
## How It Works
 
1. **Fetch:** Synchronizes with the gPodder database to find your latest feeds.
2. **Download:** Automatically grabs the latest unplayed episodes from the current month.
3. **Analyze:** Uploads raw audio to Gemini 3.1 for native summarization.
4. **Chat:** The agent enters an "Interrupt" state. You can "wake" it via the UI to ask questions about the episode.
5. **Save:** All metadata, summaries, and chat history are persisted in `checkpoints.db`.
 
---
 
## Background Automation
 
To keep your summaries ready before you even wake up, set up a cron job:
 
```bash
# Check for new episodes every 4 hours
(crontab -l 2>/dev/null; echo "0 */4 * * * /path/to/poddersum/run_agent.sh") | crontab -
```
 
---
 
## License
 
Distributed under the MIT License. See `LICENSE` for more information.
