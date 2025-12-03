#!/usr/bin/env python3
"""
mem-server.py - HTTP API server for cross-machine memory access

Endpoints:
  GET  /health          - Server health check
  GET  /briefing        - Generate session briefing
  GET  /query?t=d&...   - Query memory entries
  POST /write           - Write new memory entry
  GET  /status          - Database status
  POST /semantic        - Semantic search (if embeddings available)
  POST /llm             - LLM proxy (Windows can call without local API key)

Run:
  ./mem-server.py --port 8765 --host 0.0.0.0
"""

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import argparse

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "memory.db"
MEM_DB_SH = SCRIPT_DIR / "mem-db.sh"

class MemoryAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for memory API."""

    def _send_json(self, data, status=200):
        """Send JSON response."""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_error(self, message, status=400):
        """Send error response."""
        self._send_json({'error': message}, status)

    def _get_db(self):
        """Get database connection."""
        return sqlite3.connect(str(DB_PATH))

    def _run_mem_db(self, *args):
        """Run mem-db.sh command and return output."""
        try:
            result = subprocess.run(
                [str(MEM_DB_SH)] + list(args),
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", "Command timed out", 1
        except Exception as e:
            return "", str(e), 1

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # Flatten single-value params
        params = {k: v[0] if len(v) == 1 else v for k, v in params.items()}

        if path == '/health':
            self._handle_health()
        elif path == '/briefing':
            self._handle_briefing(params)
        elif path == '/query':
            self._handle_query(params)
        elif path == '/status':
            self._handle_status()
        elif path == '/render':
            self._handle_render(params)
        else:
            self._send_error(f"Unknown endpoint: {path}", 404)

    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        # Read body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode() if content_length else '{}'

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_error("Invalid JSON body")
            return

        if path == '/write':
            self._handle_write(data)
        elif path == '/semantic':
            self._handle_semantic(data)
        elif path == '/llm':
            self._handle_llm(data)
        else:
            self._send_error(f"Unknown endpoint: {path}", 404)

    def _handle_health(self):
        """Health check endpoint."""
        db_exists = DB_PATH.exists()
        if db_exists:
            try:
                conn = self._get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM chunks")
                count = cursor.fetchone()[0]
                conn.close()
                self._send_json({
                    'status': 'healthy',
                    'database': str(DB_PATH),
                    'entries': count
                })
            except Exception as e:
                self._send_json({
                    'status': 'degraded',
                    'error': str(e)
                }, 500)
        else:
            self._send_json({
                'status': 'unhealthy',
                'error': 'Database not found'
            }, 500)

    def _handle_briefing(self, params):
        """Generate session briefing."""
        try:
            # Import briefing generator
            sys.path.insert(0, str(SCRIPT_DIR))
            from importlib import import_module
            briefing_mod = import_module('mem-briefing')

            project = params.get('project')
            fmt = params.get('format', 'text')

            briefing = briefing_mod.generate_briefing(format=fmt, project=project)

            if fmt == 'json':
                self._send_json(json.loads(briefing))
            else:
                self._send_json({'briefing': briefing})
        except Exception as e:
            self._send_error(f"Briefing generation failed: {e}", 500)

    def _handle_query(self, params):
        """Query memory entries."""
        # Build args for mem-db.sh query
        args = ['query', '--json']
        for key, value in params.items():
            if key in ['t', 'type', 'topic', 'text', 'limit', 'recent', 'scope', 'chat_id', 'choice']:
                args.append(f"{key}={value}")

        stdout, stderr, code = self._run_mem_db(*args)

        if code != 0:
            self._send_error(stderr or "Query failed", 500)
            return

        # Parse JSONL output
        results = []
        for line in stdout.strip().split('\n'):
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

        self._send_json({'results': results, 'count': len(results)})

    def _handle_write(self, data):
        """Write new memory entry."""
        # Required fields
        entry_type = data.get('type') or data.get('t')
        text = data.get('text')

        if not entry_type:
            self._send_error("Missing required field: type (t)")
            return
        if not text:
            self._send_error("Missing required field: text")
            return

        # Build args
        args = ['write', f"t={entry_type}", f"text={text}"]

        # Optional fields
        optional = ['topic', 'choice', 'rationale', 'scope', 'chat_id', 'role', 'visibility', 'project', 'session', 'source']
        for field in optional:
            if field in data and data[field]:
                args.append(f"{field}={data[field]}")

        stdout, stderr, code = self._run_mem_db(*args)

        if code != 0:
            self._send_error(stderr or "Write failed", 500)
            return

        try:
            result = json.loads(stdout)
            self._send_json(result)
        except json.JSONDecodeError:
            self._send_json({'status': 'written', 'output': stdout})

    def _handle_status(self):
        """Database status."""
        stdout, stderr, code = self._run_mem_db('status')

        if code != 0:
            self._send_error(stderr or "Status failed", 500)
            return

        # Parse status output into structured data
        lines = stdout.strip().split('\n')
        self._send_json({'raw': stdout, 'lines': lines})

    def _handle_render(self, params):
        """Render entries in glyph format."""
        args = ['render']
        for key, value in params.items():
            if key in ['t', 'type', 'topic', 'text', 'limit', 'recent']:
                args.append(f"{key}={value}")

        stdout, stderr, code = self._run_mem_db(*args)

        if code != 0:
            self._send_error(stderr or "Render failed", 500)
            return

        self._send_json({'rendered': stdout, 'lines': stdout.strip().split('\n') if stdout.strip() else []})

    def _handle_semantic(self, data):
        """Semantic search."""
        query = data.get('query') or data.get('q')
        if not query:
            self._send_error("Missing required field: query (q)")
            return

        limit = data.get('limit', 10)

        stdout, stderr, code = self._run_mem_db('semantic', query, '--limit', str(limit), '--json')

        if code != 0:
            # Semantic search might not be available
            self._send_error(f"Semantic search failed: {stderr}", 500)
            return

        try:
            results = json.loads(stdout)
            self._send_json(results)
        except json.JSONDecodeError:
            self._send_json({'raw': stdout})

    def _handle_llm(self, data):
        """LLM proxy - allows Windows to call LLMs through Linux server."""
        prompt = data.get('prompt')
        if not prompt:
            self._send_error("Missing required field: prompt")
            return

        tier = data.get('tier', 'fast')
        timeout = data.get('timeout', 120)

        try:
            # Import LLM client
            sys.path.insert(0, str(SCRIPT_DIR))
            from llm_client import LLMClient

            client = LLMClient()
            result = client.complete(prompt, tier=tier, timeout=timeout)

            self._send_json({
                'success': result.success,
                'text': result.text,
                'provider': result.provider,
                'model': result.model,
                'tier': result.tier,
                'latency_ms': result.latency_ms,
                'error': result.error
            })
        except Exception as e:
            self._send_error(f"LLM call failed: {e}", 500)

    def log_message(self, format, *args):
        """Custom log format."""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description='Memory API Server')
    parser.add_argument('--port', type=int, default=8765, help='Port to listen on')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), MemoryAPIHandler)
    print(f"Memory API server starting on http://{args.host}:{args.port}")
    print(f"Database: {DB_PATH}")
    print(f"Endpoints: /health, /briefing, /query, /write, /status, /render, /semantic")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == '__main__':
    main()
