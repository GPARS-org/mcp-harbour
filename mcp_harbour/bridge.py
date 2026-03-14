"""
MCP Harbour Bridge — lightweight stdio-to-daemon proxy for agents.

This module has NO admin dependencies. It does not import keyring, bcrypt,
rich, config management, or any server-side logic. It only needs asyncio
and the ability to open a TCP connection.
"""

import sys
import json
import asyncio
import argparse


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4767


async def run_bridge(token: str, host: str, port: int):
    try:
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except ConnectionRefusedError:
            sys.stderr.write(f"Cannot connect to {host}:{port}\n")
            sys.stderr.write("Is the daemon running? (Run 'harbour start')\n")
            sys.exit(1)

        # Handshake: token only
        handshake = json.dumps({"auth": token}).encode() + b"\n"
        writer.write(handshake)
        await writer.drain()

        ack_line = await reader.readline()
        try:
            ack = json.loads(ack_line.decode())
            if "error" in ack:
                sys.stderr.write(f"Connection Refused: {ack['error']}\n")
                sys.exit(1)
            if ack.get("status") != "ok":
                sys.stderr.write(f"Unknown handshake response: {ack_line.decode()}\n")
                sys.exit(1)
        except (json.JSONDecodeError, KeyError):
            sys.stderr.write(f"Invalid handshake response: {ack_line}\n")
            sys.exit(1)

        # Bidirectional pipe: stdin ↔ daemon
        loop = asyncio.get_running_loop()

        stdin_reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(stdin_reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        w_transport, w_protocol = await loop.connect_write_pipe(
            lambda: asyncio.StreamReaderProtocol(asyncio.StreamReader()), sys.stdout
        )
        stdout_writer = asyncio.StreamWriter(w_transport, w_protocol, None, loop)

        async def pipe(r, w):
            try:
                while True:
                    data = await r.read(4096)
                    if not data:
                        break
                    w.write(data)
                    await w.drain()
            except Exception:
                pass
            finally:
                try:
                    w.close()
                except Exception:
                    pass

        try:
            await asyncio.gather(
                pipe(stdin_reader, writer), pipe(reader, stdout_writer)
            )
        except asyncio.CancelledError:
            pass
        finally:
            writer.close()

    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"Bridge Error: {e}\n")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="harbour-bridge",
        description="MCP Harbour Bridge — connects an agent to the Harbour daemon.",
    )
    parser.add_argument(
        "--token", required=True, help="API key for authentication"
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"Daemon host (default: {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Daemon port (default: {DEFAULT_PORT})"
    )

    args = parser.parse_args()
    asyncio.run(run_bridge(args.token, args.host, args.port))


if __name__ == "__main__":
    main()
