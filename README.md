# Mubi ReviewFlow

Mubi ReviewFlow is a research review and evidence synthesis workspace. The project contains a browser-based frontend, a FastAPI backend, the `coscreen` analysis and review modules, and Windows packaging scripts.

## Project layout

- `custom_frontend/` — browser interface and bundled PDF.js viewer.
- `custom_backend/` — FastAPI application and API services.
- `coscreen/` — review workflow, evidence coding, statistical analysis, and export modules.
- `build/` — Windows launcher and installer build materials.
- `README-Windows.md` — Windows build, installation, operation, and acceptance notes.
- `THIRD_PARTY_NOTICES.md` — third-party components and license notices.

## Windows build

See [README-Windows.md](README-Windows.md) for the Windows build and installation workflow. The folder contains build materials; it does not include a generated installer.

## Development

The backend uses Python and FastAPI. Install the dependencies listed in `build/requirements-app.txt`, then start the backend with the project's launcher or with Uvicorn using `custom_backend.main:app`. Keep authentication secrets and research data in local runtime storage; do not commit them to this repository.

Third-party components retain their respective licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the notices shipped with each component.

## License

This project is distributed under the MIT License. See [LICENSE](LICENSE).
