import os
from typing import Annotated, TypedDict, Literal
import operator
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command
from google import genai
from google.genai import types

import gpodder_utils
import gemini_utils

load_dotenv()

# State definition
class AgentState(TypedDict):
    episode_id: int
    podcast_title: str
    episode_title: str
    audio_url: str
    audio_path: str
    gemini_file_uri: str
    summary: str
    messages: Annotated[list, operator.add]
    status: str

# Nodes
def download_node(state: AgentState) -> dict:
    if state.get("audio_path") and os.path.exists(state["audio_path"]):
        return {"status": "Downloaded"}
    
    details = gpodder_utils.get_episode_details(state["episode_id"])
    p_title, e_title, url, _ = details
    
    path = gemini_utils.download_file(url, p_title, e_title)
    return {
        "audio_path": path,
        "podcast_title": p_title,
        "episode_title": e_title,
        "audio_url": url,
        "status": "Downloaded"
    }

def summarize_node(state: AgentState) -> dict:
    if state.get("summary"):
        return {"status": "Summarized"}
    
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    
    # Upload if not already uploaded
    if not state.get("gemini_file_uri"):
        file_upload = gemini_utils.upload_to_gemini(client, state["audio_path"])
        gemini_file_uri = file_upload.uri
    else:
        gemini_file_uri = state["gemini_file_uri"]

    prompt = f"""
    You are an expert podcast summarizer. Please provide a detailed summary of the following podcast episode.
    Podcast: {state['podcast_title']}
    Episode: {state['episode_title']}

    Follow this template:
    # {state['episode_title']}
    ## Metadata
    - **Podcast:** {state['podcast_title']}
    - **Episode:** {state['episode_title']}

    ## Core Argument
    Describe the primary thesis or the "why" behind this episode.

    ## Top 3 Takeaways
    The most important lessons or "lightbulb moments" from the conversation.

    ## Books & Resources
    A dedicated section for every book, paper, or tool mentioned. List them clearly.

    ## Named Entities
    Categorize People, Companies, and Locations mentioned.

    ## Segment Summaries
    Break down the conversation into logical segments with a short summary for each.
    """

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_uri(file_uri=gemini_file_uri, mime_type="audio/mpeg"),
                    types.Part.from_text(text=prompt)
                ]
            )
        ]
    )

    return {
        "summary": response.text,
        "gemini_file_uri": gemini_file_uri,
        "status": "Summarized"
    }

def agent_chat_node(state: AgentState) -> Command[Literal["agent_chat_node", "__end__"]]:
    """Interactive node for user to ask questions about the podcast."""
    
    # Check if we have a user message to process
    if state["messages"] and state["messages"][-1]["role"] == "user":
        user_msg = state["messages"][-1]["content"]
        
        api_key = os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)
        
        prompt = f"""
        You are an assistant who has access to the podcast "{state['episode_title']}".
        The user has already seen the summary:
        {state['summary']}
        
        Answer the user's question based on the audio content.
        """
        
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_uri(file_uri=state['gemini_file_uri'], mime_type="audio/mpeg"),
                        types.Part.from_text(text=prompt + "\n\nUser Question: " + user_msg)
                    ]
                )
            ]
        )
        
        return Command(
            update={"messages": [{"role": "assistant", "content": response.text}]},
            goto="agent_chat_node" # Stay in chat loop
        )
    
    # If no user message, interrupt and wait for one
    user_input = interrupt("Waiting for user question...")
    
    return Command(
        update={"messages": [{"role": "user", "content": user_input}]},
        goto="agent_chat_node"
    )

# Graph setup
import sqlite3
conn = sqlite3.connect("checkpoints.db", check_same_thread=False)
memory = SqliteSaver(conn)

builder = StateGraph(AgentState)
builder.add_node("download", download_node)
builder.add_node("summarize", summarize_node)
builder.add_node("agent_chat_node", agent_chat_node)

builder.add_edge(START, "download")
builder.add_edge("download", "summarize")
builder.add_edge("summarize", "agent_chat_node")

graph = builder.compile(checkpointer=memory)

def get_graph():
    return graph
