"""Command line: ``evidencia serve`` and ``evidencia mcp``."""
import argparse
import logging
import os


def main(argv=None):
    parser = argparse.ArgumentParser(prog="evidencia")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--config", help="TOML config file (default: $EVIDENCIA_CONFIG)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    mcp = sub.add_parser("mcp", help="run an MCP server on stdio that asks a running evidencia service")
    mcp.add_argument("--url", default=os.environ.get("EVIDENCIA_URL", "http://127.0.0.1:8080"),
                     help="the evidencia service (default: $EVIDENCIA_URL or http://127.0.0.1:8080)")
    mcp.add_argument("--key", default=os.environ.get("EVIDENCIA_API_KEY"),
                     help="the service's bearer token (default: $EVIDENCIA_API_KEY)")
    args = parser.parse_args(argv)

    if args.command == "mcp":
        from evidencia.mcp_server import Service, build_server
        build_server(Service(args.url, args.key)).run("stdio")
        return

    import uvicorn
    from evidencia.api import create_app
    from evidencia.config import Config

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(Config.load(args.config)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
