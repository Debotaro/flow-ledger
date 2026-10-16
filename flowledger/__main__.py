import argparse

import uvicorn

from .app import create_app
from .config import Settings


def main():
    parser = argparse.ArgumentParser(description="Run FlowLedger")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    settings = Settings()
    if args.host not in ("127.0.0.1", "localhost", "::1") and not settings.token:
        parser.error("Binding to a remote interface requires FLOW_API_TOKEN (24+ characters).")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, limit_concurrency=32)


if __name__ == "__main__":
    main()
