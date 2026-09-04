import uvicorn

from emi.api import app
from emi.settings import Settings

settings = Settings()


def main() -> None:
    uvicorn.run(app, host=settings.api_host,
                port=settings.api_port, reload=False)


if __name__ == "__main__":
    main()
