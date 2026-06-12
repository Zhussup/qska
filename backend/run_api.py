"""Convenience runner: uvicorn qsmeta.backend.app.main:app --reload"""
import os
import sys

if __name__ == "__main__":
    import uvicorn
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from app import config
    config.load_env()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
