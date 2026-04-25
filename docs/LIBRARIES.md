# Library Stack and Tooling Notes

This document covers the project-managed runtime libraries declared in `pyproject.toml`, the optional extras used during experiments or development, and the most important indirect dependencies that appear in the geometry stack.

Branding note: the product/docs are branded as Geekatplay Studio, but package names, environment variables, and some internal modules still use `resinlogic` compatibility identifiers.

## Direct Runtime Dependencies

| Dependency | Where it is used | Why it exists |
| --- | --- | --- |
| `fastapi` | `app/main.py` and route modules | HTTP API layer, dependency injection, response modeling, and static UI serving. |
| `uvicorn` | local startup and Docker `CMD` | ASGI server for development and deployment. |
| `apscheduler` | `app/scheduler.py`, schedule execution in `app/main.py` | Persistent background execution of saved catalog refresh schedules. |
| `numpy` | `app/geometry.py`, `app/logic.py` | Vectorized geometry math, normals/area calculations, risk scoring, and metric normalization. |
| `pandas` | import/export and feedback pipelines | Structured data wrangling for CSV/JSON catalog flows and feedback ingestion. |
| `python-multipart` | upload endpoints in `app/main.py` | Required for multipart form handling for STL and CSV uploads. |
| `trimesh[easy]` | `app/geometry.py`, repair/voxel/slicing paths | Core mesh I/O, repair helpers, slicing, voxelization, and geometry calculations. |
| `pyvista` | deeper geometry/detail analysis paths | Higher-cost surface/detail probes used when richer geometric interpretation is needed. |

## Development and Optional Extras

| Extra | Dependency | Why it exists |
| --- | --- | --- |
| `dev` | `pytest` | Regression suite for API, catalog, sync, wizard, and geometry behavior. |
| `dev` | `httpx` | Test client support and HTTP-oriented regression helpers. |
| `mesh-repair` | `pymeshlab` | Optional prototype repair backend to compare against the Trimesh-first path. |
| `open3d-spike` | `open3d` | Optional voxel-backend experiments and benchmark scripts. |
| `sources` | `youtube-transcript-api` | Transcript ingestion for feedback-source experiments. |

## Common Indirect Dependencies in the Geometry Stack

These are not declared directly in `pyproject.toml`, but they commonly appear because of `trimesh[easy]`, `pyvista`, or other geometry-focused packages in the full runtime environment.

| Dependency | Why it matters here |
| --- | --- |
| `pydantic` | FastAPI request/response validation and schema serialization for analysis, wizard, and catalog models. |
| `starlette` | FastAPI's ASGI foundation, request handling, static files, and responses. |
| `vtk` | Render/data engine under `pyvista`. |
| `scipy` | Numeric helpers often used by mesh operations and neighborhood queries. |
| `rtree` | Spatial indexing used by mesh and geometry lookups. |
| `networkx` | Graph operations that some mesh tooling and import paths rely on. |
| `jsonschema` | Validation utilities in mesh/scene import ecosystems. |
| `lxml` | Robust XML/HTML parsing support used by some importers. |
| `pycollada` | COLLADA import/export helpers reachable through mesh tooling. |
| `mapbox_earcut` | Polygon triangulation support. |
| `manifold3d` | Robust mesh boolean/manifold helpers when present. |
| `embreex` | Accelerated ray-tracing support for geometry queries when available. |
| `vhacdx` | Convex decomposition helpers available in richer geometry environments. |

## External Tools Used Alongside Python Libraries

| Tool | Where it is used | Why it exists |
| --- | --- | --- |
| Blender | `app/geometry.py`, retopology flow, Docker image | Headless quad/voxel remesh execution for wizard retopology. |
| Docker / Docker Compose | `Dockerfile`, `docker-compose.yml` | Reproducible containerized runtime and Prometheus sidecar orchestration. |
| Prometheus | `ops/prometheus.yml` and compose service | Metrics scraping for `/ops/metrics/prometheus`. |

## Dependency Boundaries

- The libraries above describe the dependencies the project explicitly manages or relies on operationally.
- Python dependencies are declared in `pyproject.toml`; if a package is not declared there, it is either transitive or environment-specific.
- The most critical production paths are the FastAPI API layer, the SQLite-backed stores, the Trimesh-first geometry path, and the APScheduler-backed sync scheduler.
- Optional extras (`mesh-repair`, `open3d-spike`, `sources`) are intentionally separated so the default install remains lighter.

## Where To Look In Code

- `app/main.py`: app startup, route registration, wizard/runtime orchestration.
- `app/geometry.py`: mesh analysis, repair, voxelization, support heuristics.
- `app/logic.py`: settings generation for MSLA and FDM.
- `app/chitubox.py` and `app/cura.py`: slicer export rendering.
- `app/sync_service.py` and `app/web_catalog_scraper.py`: remote JSON sync and supported page scraping.
- `app/catalog_store.py`: SQLite catalog persistence.