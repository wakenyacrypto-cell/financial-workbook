import os
import webbrowser
from livereload import Server
from flask import Flask, send_from_directory

# Import the existing backend Flask app
# Ensure this import does not trigger app.run when importing
try:
    from backend.app import app as api_app
except Exception as e:
    raise RuntimeError(f"Failed to import backend.app: {e}")

# Directory containing frontend static files
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
WEB_DIR = os.path.join(ROOT_DIR, 'web')

# Create a simple Flask app to serve static files
front_app = Flask('frontend_static', static_folder=WEB_DIR, static_url_path='')

@front_app.route('/', defaults={'path': 'index.html'})
@front_app.route('/<path:path>')
def serve_frontend(path):
    # Serve files from the web directory; default to index.html
    if path == '':
        path = 'index.html'
    if os.path.exists(os.path.join(WEB_DIR, path)):
        return send_from_directory(WEB_DIR, path)
    # fall back to index.html for SPA routing
    return send_from_directory(WEB_DIR, 'index.html')

# Composite WSGI app: route /api/* to the backend app, everything else to front_app
def composite_app(environ, start_response):
    path = environ.get('PATH_INFO', '')
    if path.startswith('/api'):
        return api_app.wsgi_app(environ, start_response)
    return front_app.wsgi_app(environ, start_response)

def start_dev_server(port=5500, open_browser=True):
    server = Server(composite_app)

    # Watch frontend files for changes
    server.watch(os.path.join(WEB_DIR, '*.html'))
    server.watch(os.path.join(WEB_DIR, '*.css'))
    server.watch(os.path.join(WEB_DIR, '*.js'))
    server.watch(os.path.join(WEB_DIR, '**', '*.html'))
    server.watch(os.path.join(WEB_DIR, '**', '*.css'))
    server.watch(os.path.join(WEB_DIR, '**', '*.js'))

    url = f'http://0.0.0.0:{port}/'
    print(f"Starting development server at {url}")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    server.serve(host='0.0.0.0', port=port, root=WEB_DIR, live_css=True)

if __name__ == '__main__':
    port = int(os.environ.get('DEV_PORT', 5500))
    open_flag = os.environ.get('DEV_OPEN', '1') in ('1', 'true', 'True')
    start_dev_server(port=port, open_browser=open_flag)
