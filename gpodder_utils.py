import os
import sqlite3
import datetime
import feedparser
from langsmith import traceable

DB_PATH = "/home/aswinmanohar/gPodder/Database"

@traceable(name="Subscribe to Podcast Feed")
def subscribe_to_podcast(feed_url):
    """Manually insert a new podcast subscription into the gPodder database."""
    # 1. Parse feed for metadata
    feed = feedparser.parse(feed_url)
    if not feed.feed:
        return False, "Invalid RSS feed URL."
    
    title = feed.feed.get("title", "Unknown Podcast")
    link = feed.feed.get("link", "")
    description = feed.feed.get("description", "")
    # feedparser stores images in 'image' dict
    cover_url = ""
    if hasattr(feed.feed, 'image'):
        cover_url = feed.feed.image.get("href", "")

    # 2. Check if already subscribed
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM podcast WHERE url = ?", (feed_url,))
    if cursor.fetchone():
        conn.close()
        return False, f"Already subscribed to '{title}'."

    # 3. Insert into database
    try:
        query = """
        INSERT INTO podcast (title, url, link, description, cover_url)
        VALUES (?, ?, ?, ?, ?)
        """
        cursor.execute(query, (title, feed_url, link, description, cover_url))
        conn.commit()
        
        # After inserting a podcast, gPodder typically needs to fetch episodes.
        # However, our script can do that manually later or wait for gPodder GUI.
        
        conn.close()
        return True, f"Successfully subscribed to '{title}'."
    except Exception as e:
        if conn:
            conn.close()
        return False, f"Database error: {str(e)}"

@traceable(name="Fetch New Episodes")
def fetch_episodes(podcast_url=None):
    """Fetch new episodes for one or all podcasts and add them to the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if podcast_url:
        cursor.execute("SELECT id, url FROM podcast WHERE url = ?", (podcast_url,))
    else:
        cursor.execute("SELECT id, url FROM podcast")
    
    podcasts = cursor.fetchall()
    
    new_count = 0
    for p_id, url in podcasts:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            # Get audio URL (usually in enclosures)
            audio_url = ""
            for link in entry.get("links", []):
                if "audio" in link.get("type", ""):
                    audio_url = link.get("href", "")
                    break
            
            if not audio_url and "enclosures" in entry:
                for enc in entry.enclosures:
                    if "audio" in enc.get("type", ""):
                        audio_url = enc.get("href", "")
                        break
            
            if not audio_url:
                continue

            guid = entry.get("id", entry.get("link", audio_url))
            title = entry.get("title", "Untitled Episode")
            description = entry.get("summary", "")
            # Convert published time to timestamp
            published = 0
            if "published_parsed" in entry and entry.published_parsed:
                published = int(datetime.datetime(*entry.published_parsed[:6]).timestamp())
            
            # Check if episode already exists
            cursor.execute("SELECT id FROM episode WHERE podcast_id = ? AND guid = ?", (p_id, guid))
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO episode (podcast_id, title, description, url, published, guid, state, is_new)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 1)
                """, (p_id, title, description, audio_url, published, guid))
                new_count += 1
    
    conn.commit()
    conn.close()
    return new_count

def get_subscriptions():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, url FROM podcast")
    podcasts = cursor.fetchall()
    conn.close()
    return podcasts

def get_latest_episodes(limit=20):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    query = """
    SELECT p.title as podcast_title, e.title as episode_title, e.url, e.published, e.id, e.state
    FROM episode e
    JOIN podcast p ON e.podcast_id = p.id
    ORDER BY e.published DESC
    LIMIT ?
    """
    cursor.execute(query, (limit,))
    episodes = cursor.fetchall()
    conn.close()
    return episodes

def get_episode_details(episode_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    query = """
    SELECT p.title, e.title, e.url, e.published
    FROM episode e
    JOIN podcast p ON e.podcast_id = p.id
    WHERE e.id = ?
    """
    cursor.execute(query, (episode_id,))
    details = cursor.fetchone()
    conn.close()
    return details
