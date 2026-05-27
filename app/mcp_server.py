import os
import sys
import json
from typing import Optional
from mcp.server.fastmcp import FastMCP

# Ensure the parent directory is in the path to import transcription pipeline
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Initialize FastMCP Server
mcp = FastMCP("TranscriptionAPI-MCP")

@mcp.tool()
def transcribe_media(
    file_path: Optional[str] = None,
    url: Optional[str] = None,
    model: Optional[str] = None
) -> str:
    """Transcribe video or audio content to structured, diarized English text.
    
    You must provide either a local file_path OR a public url.
    
    Args:
        file_path: Path to a local media file (e.g. video.mp4, audio.wav) on the system.
        url: Public HTTP(S) link to a video or audio file.
        model: Optional model override (e.g. 'gemini-2.5-pro' to bypass diarization fallback).
    """
    if not file_path and not url:
        return "Error: You must provide either 'file_path' or 'url' to transcribe."
        
    try:
        from transcribe import transcribe
        # Run transcription pipeline
        result = transcribe(input_path=file_path, url=url, model=model)
        
        # Return formatted JSON string
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error executing transcription pipeline: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
