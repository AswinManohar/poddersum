import streamlit as st
import os
from dotenv import load_dotenv
import gpodder_utils
from graph_engine import graph
from langgraph.types import Command
import uuid

load_dotenv()

st.set_page_config(page_title="Podcast Summarizer Agent", layout="wide")

st.title("🎙️ Podcast Summarizer Agent")

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
        # Initial run
        with st.spinner("Downloading and Summarizing..."):
            res = graph.invoke({"episode_id": ep["id"], "messages": []}, config)
            current_state = graph.get_state(config)

    # Display Summary
    if current_state.values.get("summary"):
        st.markdown(current_state.values["summary"])
    
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
