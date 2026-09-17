import os
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from src.core.config import LOG_PATH

router = APIRouter(tags=["Logs"])

@router.post("/api/log")
async def log_search_query(payload: dict):
    query = payload.get("query", "").strip()
    if query:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {query}\n")
        except Exception as e:
            print(f"Failed to write search log: {e}")
    return {"status": "ok"}

@router.get("/api/log")
@router.get("/log")
async def get_search_logs():
    if not os.path.exists(LOG_PATH):
        content = "<li>No searches logged yet.</li>"
    else:
        try:
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            lines.reverse()
            formatted_lines = []
            for line in lines:
                if line.startswith("[") and "]" in line:
                    idx = line.find("]")
                    time_part = line[1:idx]
                    query_part = line[idx+1:].strip()
                    formatted_lines.append(f'<li><span class="time">{time_part}</span><span class="query">{query_part}</span></li>')
                else:
                    formatted_lines.append(f'<li>{line}</li>')
            content = "".join(formatted_lines[:2000])
        except Exception as e:
            content = f"<li>Error reading log: {e}</li>"
            
    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>HYU Query Streams</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: #0a0a0a;
                color: #ffffff;
                padding: 2rem;
                margin: 0;
            }}
            .container {{
                max-width: 800px;
                margin: 0 auto;
            }}
            h1 {{
                font-size: 1.5rem;
                font-weight: 400;
                text-transform: uppercase;
                letter-spacing: 0.1em;
                border-bottom: 1px solid #222;
                padding-bottom: 10px;
                margin-bottom: 20px;
            }}
            ul {{
                list-style-type: none;
                padding: 0;
                margin: 0;
            }}
            li {{
                background-color: #121212;
                border: 1px solid #1c1c1c;
                padding: 12px 16px;
                margin-bottom: 8px;
                font-size: 0.9rem;
                font-family: monospace;
                letter-spacing: 0.05em;
                display: flex;
                justify-content: space-between;
            }}
            .time {{ color: #888; margin-right: 20px; }}
            .query {{ color: #4ade80; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>HYU Transaction Query Logs</h1>
            <ul>
                {content}
            </ul>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_template, status_code=200)
