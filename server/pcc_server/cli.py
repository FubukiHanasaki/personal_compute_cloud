import uvicorn


def main():
    uvicorn.run("pcc_server.app:create_app", factory=True, host="0.0.0.0", port=8000)
