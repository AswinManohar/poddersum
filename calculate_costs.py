import os
import sqlite3

# Configuration
DB_PATH = "checkpoints.db"

# Gemini 2.0 / 3.0 Flash Estimated Pricing
# Input: $0.10 / 1M tokens, Output: $0.40 / 1M tokens
PRICING_INPUT = 0.10 / 1_000_000
PRICING_OUTPUT = 0.40 / 1_000_000

def calculate_total_costs():
    if not os.path.exists(DB_PATH):
        print(f"No checkpoint database found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # We need to extract the 'usage' data from the checkpoints
    # LangGraph stores state in the 'checkpoints' table, but the 'usage' 
    # field we added is inside the serialized 'checkpoint' blob.
    # However, since we are already calculating this in the Streamlit UI,
    # we can create a standalone script that mimics that logic.
    
    # Note: Accessing the serialized blob directly is complex, so for a 
    # standalone tool, the most reliable way is to query the graph state 
    # via the LangGraph library.
    
    from graph_engine import graph
    
    print(f"{'Podcast Episode':<60} | {'Tokens':<10} | {'Cost ($)':<10}")
    print("-" * 85)
    
    total_all_tokens = 0
    total_all_cost = 0.0
    
    # Get all unique thread IDs (conversations) from the DB
    cursor.execute("SELECT DISTINCT thread_id FROM checkpoints")
    threads = cursor.fetchall()
    
    for (thread_id,) in threads:
        config = {"configurable": {"thread_id": thread_id}}
        state = graph.get_state(config)
        
        if state.values and "usage" in state.values:
            usage_list = state.values["usage"]
            p_title = state.values.get("podcast_title", "Unknown")
            e_title = state.values.get("episode_title", "Unknown")
            
            prompt_tokens = sum(u["prompt_token_count"] for u in usage_list)
            candidate_tokens = sum(u["candidates_token_count"] for u in usage_list)
            total_tokens = sum(u["total_token_count"] for u in usage_list)
            
            cost = (prompt_tokens * PRICING_INPUT) + (candidate_tokens * PRICING_OUTPUT)
            
            display_name = f"{p_title}: {e_title}"[:57] + "..." if len(f"{p_title}: {e_title}") > 60 else f"{p_title}: {e_title}"
            print(f"{display_name:<60} | {total_tokens:<10,} | ${cost:.6f}")
            
            total_all_tokens += total_tokens
            total_all_cost += cost
            
    print("-" * 85)
    print(f"{'TOTAL':<60} | {total_all_tokens:<10,} | ${total_all_cost:.4f}")
    
    conn.close()

if __name__ == "__main__":
    calculate_total_costs()
