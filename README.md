# Podcast Summarizer Agentic System

poddersum is an agentic podcast summarizer built with **LangGraph** for an agentic workflow and **Gemini 2.0 Flash** for native audio summarization and transcription.

---

# 🎙️ Beginner's Guide (Linux & Mac)

Follow these steps to set up your own personal AI podcast assistant.

### Step 1: Install "The Librarian" (gPodder)
The app uses a free tool called **gPodder** to keep track of your podcasts.
*   **Mac:** [Download gPodder here](https://gpodder.github.io/) and drag it to your Applications.
*   **Linux:** Open your "Software Store" and search for **gPodder**, or type `sudo apt install gpodder` in a terminal.
*   **Open gPodder once** so it creates its database.

### Step 2: Get your AI "Key"
To use the Gemini AI, you need a free API key.
1.  Go to the [Google AI Studio](https://aistudio.google.com/app/apikey).
2.  Click **"Create API key"**.
3.  Copy that key and keep it safe—you’ll need it in a moment.

### Step 3: Setup the App
Open the folder where this project is located on your computer and follow these simple steps:
1.  **Open Terminal:** (On Mac, press `Cmd + Space` and type "Terminal". On Linux, press `Ctrl + Alt + T`).
2.  **Go to the folder:** Type `cd` followed by a space, then drag the project folder into the terminal window and press **Enter**.
3.  **Install requirements:** Copy and paste this line and press **Enter**:
    ```bash
    pip install streamlit google-genai langgraph langgraph-checkpoint-sqlite feedparser python-dotenv requests
    ```
4.  **Add your Key:** Create a file in that folder named `.env` and paste your key inside like this:
    ```text
    GEMINI_API_KEY=your_key_here
    ```

### Step 4: How to Use the App
Every time you want to use the app, just type this in your terminal (inside the project folder):
```bash
streamlit run app.py
```
A new tab will open in your web browser with your dashboard!

---

### ➕ How to Add Your Favorite Podcast
You have two ways to add podcasts:

#### Option A: Inside the App (Easiest)
1.  In the app's sidebar (on the left), find the **"Subscribe"** section.
2.  Paste the **RSS URL** of your favorite podcast.
3.  Click **"Subscribe"**. The app will automatically fetch the latest episodes!

#### Option B: Using gPodder
1.  Open the **gPodder** app you installed in Step 1.
2.  Go to `Podcasts -> Subscribe to new podcast via URL`.
3.  Any podcast you add here will automatically show up in the AI app the next time you refresh it!

---

## Features
- **Agentic Workflow:** Uses LangGraph to manage the download, summarization, and interactive chat phases.
- **Persistent Memory:** Uses `SqliteSaver` to remember conversation history and summary state across sessions.
- **Interactive Front-end:** A Streamlit app to browse podcasts, generate summaries, and chat with the agent.
- **Full Transcription:** High-quality verbatim transcripts with speaker name identification.
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
3. **Summarize:** Uploads audio to Gemini 2.0 Flash, which generates a rich Markdown summary.
4. **Transcribe:** Generates a full transcript using Gemini 2.0 Flash with speaker diarization.
5. **Chat:** The agent enters a "Waiting" state (interrupt) in the LangGraph. You can resume it in the UI to ask follow-up questions.
6. **Persistence:** Everything is saved in `checkpoints.db`.
7. **Cost Tracking:** Token usage and estimated costs are tracked and displayed in the dashboard.
