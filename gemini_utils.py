import os
import requests
from google import genai
from google.genai import types
import time

DOWNLOADS_DIR = "/home/aswinmanohar/poddersum/downloads"

def download_file(url, podcast_title, episode_title):
    if not os.path.exists(DOWNLOADS_DIR):
        os.makedirs(DOWNLOADS_DIR)
    
    # Safe filename
    clean_podcast = "".join([c for c in podcast_title if c.isalnum() or c in (' ', '_')]).rstrip()
    clean_episode = "".join([c for c in episode_title if c.isalnum() or c in (' ', '_')]).rstrip()
    filename = f"{clean_podcast}_{clean_episode}".replace(" ", "_")[:100] + ".mp3"
    filepath = os.path.join(DOWNLOADS_DIR, filename)
    
    if os.path.exists(filepath):
        return filepath

    print(f"Downloading: {url}")
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()
    
    with open(filepath, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    
    return filepath

def upload_to_gemini(client, file_path):
    print(f"Uploading {file_path} to Gemini...")
    file_upload = client.files.upload(file=file_path)
    
    while file_upload.state.name == "PROCESSING":
        time.sleep(5)
        file_upload = client.files.get(name=file_upload.name)
    
    if file_upload.state.name == "FAILED":
        raise Exception(f"File processing failed: {file_upload.error}")
        
    return file_upload
