"""
Entry point — start the Narrative Radar dashboard + scheduler.

    python main.py
"""
import uvicorn
from config import PORT

if __name__ == "__main__":
    uvicorn.run(
        "dashboard.app:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="warning",   # suppress uvicorn noise; we use our own logger
    )
