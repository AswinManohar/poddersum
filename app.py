import streamlit as st
import os
from dotenv import load_dotenv
import gpodder_utils
from graph_engine import graph
from langgraph.types import Command
import uuid

load_dotenv()

SUMMARIES_DIR = "/home/aswinmanohar/poddersum/summaries"
TRANSCRIPTIONS_DIR = "/home/aswinmanohar/poddersum/transcriptions"

st.set_page_config(page_title="Podcast Summarizer Agent", layout="wide")

st.title("🎙️ Podcast Summarizer Agent")

# Global Cost Calculation
def get_global_usage():
    import sqlite3
    conn = sqlite3.connect("checkpoints.db")
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT thread_id FROM checkpoints")
    threads = cursor.fetchall()
    
    total_prompt = 0
    total_candidates = 0
    
    for (thread_id,) in threads:
        config = {"configurable": {"thread_id": thread_id}}
        state = graph.get_state(config)
        if state.values and "usage" in state.values:
            usage_list = state.values["usage"]
            total_prompt += sum(u["prompt_token_count"] for u in usage_list)
            total_candidates += sum(u["candidates_token_count"] for u in usage_list)
    
    conn.close()
    return total_prompt, total_candidates

# Display Global Metrics
try:
    g_prompt, g_candidates = get_global_usage()
    g_total_tokens = g_prompt + g_candidates
    g_cost = (g_prompt * 0.10 / 1_000_000) + (g_candidates * 0.40 / 1_000_000)
    
    m_col1, m_col2, m_col3 = st.columns(3)
    m_col1.metric("Total Tokens Processed", f"{g_total_tokens:,}")
    m_col2.metric("Total Estimated Cost", f"${g_cost:.4f}")
    m_col3.metric("Episodes Summarized", len(set([f for f in os.listdir(SUMMARIES_DIR) if f.endswith(".md")])) if os.path.exists(SUMMARIES_DIR) else 0)
    st.divider()
except Exception:
    pass # Database might not exist yet

# Sidebar: Configuration
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Gemini API Key", value=os.environ.get("GEMINI_API_KEY", ""), type="password")
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key
    
    st.divider()
    
    # Usage & Cost Transparency
    if "selected_episode" in st.session_state and "thread_id" in st.session_state:
        config = {"configurable": {"thread_id": st.session_state.thread_id}}
        current_state = graph.get_state(config)
        if current_state.values and "usage" in current_state.values:
            usage_list = current_state.values["usage"]
            total_prompt = sum(u["prompt_token_count"] for u in usage_list)
            total_candidates = sum(u["candidates_token_count"] for u in usage_list)
            total_tokens = sum(u["total_token_count"] for u in usage_list)
            
            # Gemini 2.0 Flash Estimated Pricing
            # Input: $0.10 / 1M tokens, Output: $0.40 / 1M tokens
            cost = (total_prompt * 0.10 / 1_000_000) + (total_candidates * 0.40 / 1_000_000)
            
            st.header("📊 Usage & Cost")
            st.write(f"**Total Tokens:** {total_tokens:,}")
            st.write(f"- Prompt: {total_prompt:,}")
            st.write(f"- Response: {total_candidates:,}")
            st.write(f"**Est. Cost:** ${cost:.6f}")
            st.caption("Pricing: $0.10/1M input, $0.40/1M output")
            st.divider()

    st.write("Browse your gPodder subscriptions and summarize the latest episodes.")
    
    st.divider()
    st.header("⚙️ Processing Options")
    should_transcribe = st.checkbox("Include Full Transcription", value=False, help="Transcribing the full audio will use more tokens.")
    
    st.divider()
    st.header("➕ Subscribe")
    new_url = st.text_input("Podcast RSS URL")
    if st.button("Subscribe"):
        if new_url:
            with st.spinner("Subscribing..."):
                success, msg = gpodder_utils.subscribe_to_podcast(new_url)
                if success:
                    st.success(msg)
                    # Automatically fetch episodes for the new podcast
                    gpodder_utils.fetch_episodes(new_url)
                    st.session_state.episodes = gpodder_utils.get_latest_episodes(limit=30)
                else:
                    st.error(msg)
    
    st.divider()
    if st.button("🔄 Refresh All Feeds"):
        with st.spinner("Fetching new episodes..."):
            count = gpodder_utils.fetch_episodes()
            st.success(f"Fetched {count} new episodes!")
            st.session_state.episodes = gpodder_utils.get_latest_episodes(limit=30)
            st.rerun()

# State initialization
if "episodes" not in st.session_state:
    st.session_state.episodes = gpodder_utils.get_latest_episodes(limit=30)

# Main UI: Episode Selection
st.subheader("Latest Episodes")

# Filter only unplayed or recent ones as per user requirement (latest in month)
# For simplicity in UI, we list them all and let user pick.
for p_title, e_title, url, published, ep_id, state in st.session_state.episodes:
    cols = st.columns([4, 1])
    cols[0].write(f"**{p_title}**: {e_title}")
    if cols[1].button("Process & Chat", key=f"btn_{ep_id}"):
        st.session_state.selected_episode = {
            "id": ep_id,
            "p_title": p_title,
            "e_title": e_title
        }
        st.session_state.thread_id = str(uuid.uuid4())
        st.rerun()

# Processing Section
if "selected_episode" in st.session_state:
    st.divider()
    ep = st.session_state.selected_episode
    st.subheader(f"Processing: {ep['e_title']}")
    
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    
    # Get current state from graph
    current_state = graph.get_state(config)
    
    if not current_state.values:
        # Initial run with progress status
        with st.status("Processing Podcast...", expanded=True) as status:
            st.write("📥 Downloading episode...")
            for step in graph.stream({
                "episode_id": ep["id"], 
                "messages": [],
                "should_transcribe": should_transcribe
            }, config, stream_mode="updates"):
                if "download" in step:
                    st.write("✅ Download complete.")
                    st.write("🧠 Summarizing audio...")
                elif "summarize" in step:
                    st.write("✅ Summarization complete.")
                    if should_transcribe:
                        st.write("✍️ Transcribing audio...")
                    else:
                        st.write("⏭️ Skipping transcription.")
                elif "transcribe" in step:
                    st.write("✅ Transcription complete.")
            
            status.update(label="✅ Processing complete!", state="complete", expanded=False)
            
            # Refresh state after stream
            current_state = graph.get_state(config)
            
            # Save to disk (mirroring main.py logic)
            summary = current_state.values.get("summary")
            transcription = current_state.values.get("transcription")
            p_title = current_state.values.get("podcast_title", ep["p_title"])
            e_title = current_state.values.get("episode_title", ep["e_title"])
            
            if summary:
                if not os.path.exists(SUMMARIES_DIR):
                    os.makedirs(SUMMARIES_DIR)
                filename = f"{p_title}_{e_title}".replace("/", "_").replace(" ", "_")[:100] + ".md"
                with open(os.path.join(SUMMARIES_DIR, filename), "w") as f:
                    f.write(summary)
            
            if transcription:
                if not os.path.exists(TRANSCRIPTIONS_DIR):
                    os.makedirs(TRANSCRIPTIONS_DIR)
                filename = f"{p_title}_{e_title}_transcript".replace("/", "_").replace(" ", "_")[:100] + ".md"
                with open(os.path.join(TRANSCRIPTIONS_DIR, filename), "w") as f:
                    f.write(transcription)

    # Display Content in Tabs
    summary = current_state.values.get("summary")
    transcription = current_state.values.get("transcription")
    
    if summary or transcription:
        tab1, tab2 = st.tabs(["📝 Summary", "📜 Full Transcription"])
        
        with tab1:
            if summary:
                st.markdown(summary)
            else:
                st.info("No summary available.")
                
        with tab2:
            if transcription:
                st.download_button(
                    label="Download Transcription (.md)",
                    data=transcription,
                    file_name=f"{ep['e_title']}_transcript.md",
                    mime="text/markdown"
                )
                st.markdown(transcription)
            elif should_transcribe:
                st.info("Transcription requested but not yet available.")
            else:
                st.info("Transcription not requested for this episode.")
    
    # Chat Interface
    st.divider()
    st.subheader("Ask the Agent about this Podcast")
    
    # Display chat history
    for msg in current_state.values.get("messages", []):
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    # Human-in-the-loop: Wait for input if graph is interrupted
    if current_state.next: # If there's a next node (meaning it's waiting)
        user_query = st.chat_input("Ask a question about the episode...")
        if user_query:
            with st.spinner("Agent is thinking..."):
                # Resume graph with user input
                graph.invoke(Command(resume=user_query), config)
                st.rerun()
    else:
        st.write("Processing finished.")

if st.button("Refresh Episodes"):
    st.session_state.episodes = gpodder_utils.get_latest_episodes(limit=30)
    st.rerun()
