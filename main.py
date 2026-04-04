import os
import datetime
from dotenv import load_dotenv
from graph_engine import graph
import gpodder_utils
from langsmith import traceable

load_dotenv()

# Relative paths for portability
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUMMARIES_DIR = os.path.join(BASE_DIR, "summaries")
TRANSCRIPTIONS_DIR = os.path.join(BASE_DIR, "transcriptions")

def get_current_month_range():
    now = datetime.datetime.now()
    start_of_month = datetime.datetime(now.year, now.month, 1)
    return int(start_of_month.timestamp())

@traceable(name="Batch Process Latest Episodes")
def process_latest_episodes():
    # 0. Fetch new episodes manually since gpo CLI is broken
    print("Fetching new episodes for all podcasts...")
    gpodder_utils.fetch_episodes()
    
    start_ts = get_current_month_range()
    
    # We want the latest episode for EACH podcast that was published THIS month and is unplayed
    # Re-using the logic from before, but calling gpodder_utils
    import sqlite3
    conn = sqlite3.connect("/home/aswinmanohar/gPodder/Database")
    cursor = conn.cursor()
    query = """
    SELECT e.id, p.title, e.title
    FROM episode e
    JOIN podcast p ON e.podcast_id = p.id
    WHERE e.published >= ? AND e.state = 0
    GROUP BY e.podcast_id
    HAVING e.published = MAX(e.published)
    """
    cursor.execute(query, (start_ts,))
    episodes = cursor.fetchall()
    conn.close()

    print(f"Found {len(episodes)} new episodes to process this month.")

    should_transcribe = os.environ.get("SHOULD_TRANSCRIBE", "false").lower() == "true"

    for ep_id, p_title, e_title in episodes:
        try:
            print(f"Processing: {e_title}")
            # Use the episode ID as the thread ID to maintain persistence for the UI
            thread_id = f"ep_{ep_id}"
            config = {"configurable": {"thread_id": thread_id}}
            
            # Check if already processed
            current_state = graph.get_state(config)
            if current_state.values.get("summary"):
                print(f"Already summarized: {e_title}")
                continue

            # Run graph to generate summary and transcription
            graph.invoke({
                "episode_id": ep_id, 
                "messages": [],
                "should_transcribe": should_transcribe
            }, config)
            
            # Save the final summary and transcription to disk
            new_state = graph.get_state(config)
            summary = new_state.values.get("summary")
            transcription = new_state.values.get("transcription")
            
            if summary:
                if not os.path.exists(SUMMARIES_DIR):
                    os.makedirs(SUMMARIES_DIR)
                
                filename = f"{p_title}_{e_title}".replace("/", "_").replace(" ", "_")[:100] + ".md"
                filepath = os.path.join(SUMMARIES_DIR, filename)
                with open(filepath, "w") as f:
                    f.write(summary)
                print(f"Summary saved: {filepath}")
            
            if transcription:
                if not os.path.exists(TRANSCRIPTIONS_DIR):
                    os.makedirs(TRANSCRIPTIONS_DIR)
                
                filename = f"{p_title}_{e_title}_transcript".replace("/", "_").replace(" ", "_")[:100] + ".md"
                filepath = os.path.join(TRANSCRIPTIONS_DIR, filename)
                with open(filepath, "w") as f:
                    f.write(transcription)
                print(f"Transcription saved: {filepath}")

        except Exception as e:
            print(f"Failed to process {e_title}: {e}")

if __name__ == "__main__":
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set.")
    else:
        process_latest_episodes()
