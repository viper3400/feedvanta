import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the FeedVanta ASGI tool host")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("tool_host.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
