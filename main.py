import os
import datetime
from dotenv import load_dotenv
from graph_engine import graph
import gpodder_utils

load_dotenv()

SUMMARIES_DIR = "/home/aswinmanohar/poddersum/summaries"

def get_current_month_range():
    now = datetime.datetime.now()
    start_of_month = datetime.datetime(now.year, now.month, 1)
    return int(start_of_month.timestamp())

def process_latest_episodes():
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

            # Run graph to generate summary
            # We invoke the graph. Since the agent_chat_node interrupts, 
            # the invoke will return after 'summarize_node' finishes and it hits the interrupt in 'agent_chat_node'.
            graph.invoke({"episode_id": ep_id, "messages": []}, config)
            
            # Save the final summary to disk
            new_state = graph.get_state(config)
            summary = new_state.values.get("summary")
            
            if summary:
                if not os.path.exists(SUMMARIES_DIR):
                    os.makedirs(SUMMARIES_DIR)
                
                filename = f"{p_title}_{e_title}".replace("/", "_").replace(" ", "_")[:100] + ".md"
                filepath = os.path.join(SUMMARIES_DIR, filename)
                with open(filepath, "w") as f:
                    f.write(summary)
                print(f"Summary saved: {filepath}")

        except Exception as e:
            print(f"Failed to process {e_title}: {e}")

if __name__ == "__main__":
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set.")
    else:
        process_latest_episodes()
