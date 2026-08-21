"""Serverless grader: GET /api/grade?codes=A,B -> plain-text grade output.

Grading fits a serverless function - it is one SportyBet share-API call per
code, a few seconds in total. The BOOKER does not fit (a board build is 15-40
minutes of Flashscore fetching), which is why the hosted page talks to a
locally-running ui.py for booking and only grading runs here.
"""
from http.server import BaseHTTPRequestHandler
import io, os, sys, contextlib
from urllib.parse import urlparse, parse_qs

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root not in sys.path:
    sys.path.insert(0, root)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        codes = (q.get('codes', [''])[0]).replace(',', ' ').split()[:8]
        buf = io.StringIO()
        if not codes:
            buf.write('no codes given')
        else:
            try:
                import grade_code as G
                with contextlib.redirect_stdout(buf):
                    for c in codes:
                        if c.isalnum() and len(c) <= 12:
                            G.grade_code(c)
            except Exception as e:
                buf.write(f'\nerror grading: {type(e).__name__}: {e}')
        body = buf.getvalue().encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
