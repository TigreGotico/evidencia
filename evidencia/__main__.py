"""Command line: ``evidencia serve``."""
import argparse
import logging


def main(argv=None):
    parser = argparse.ArgumentParser(prog="evidencia")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--config", help="TOML config file (default: $EVIDENCIA_CONFIG)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)

    import uvicorn
    from evidencia.api import create_app
    from evidencia.config import Config

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(Config.load(args.config)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
