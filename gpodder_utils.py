import os
import sqlite3
import datetime

DB_PATH = "/home/aswinmanohar/gPodder/Database"

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
