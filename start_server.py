import sys, os
# Add antenv to path
site_pkg = os.path.join(os.path.dirname(__file__), "antenv", "lib", "python3.12", "site-packages")
if os.path.exists(site_pkg):
    sys.path.insert(0, site_pkg)
sys.path.insert(0, os.path.dirname(__file__))

import uvicorn
uvicorn.run("app:app", host="0.0.0.0", port=8000, workers=1, timeout_graceful_shutdown=30)
