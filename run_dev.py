from __future__ import annotations

import uvicorn


if __name__ == "__main__":
    # The one-click process owns in-memory job state and research child processes;
    # automatic code reload would discard that state mid-run.
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=False, workers=1)
