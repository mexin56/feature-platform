"""一键启动:conda scpy310 下 python run.py,浏览器访问 http://localhost:8100"""
import uvicorn

from backend.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8100)
