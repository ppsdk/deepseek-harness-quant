"""Fresh local backend for smoke checks, stopped by a line on stdin."""
import sys
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
# Initialize numeric DLLs before the first threaded HTTP handler on Windows.
import numpy
import pandas
from deck.deck_server import ResearchHTTPServer,Handler
server=ResearchHTTPServer(('127.0.0.1',0),Handler)
Path(sys.argv[1]).write_text(str(server.server_port),encoding='utf-8')
threading.Thread(target=lambda:(sys.stdin.readline(),server.shutdown()),daemon=True).start()
try:server.serve_forever()
finally:server.server_close()
