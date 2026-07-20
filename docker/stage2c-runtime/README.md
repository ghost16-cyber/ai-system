# Astra Stage 2C runtime image

This reviewed build context creates the single local Python and Node runtime
used by isolation profile `astra-python-node-v1`. The official Node base is
pinned by immutable digest. Python, pip, and pytest are installed only while
building the image and are pinned to the recorded Debian package versions.

The context contains no repository source, credentials, package-manager
configuration, Docker socket, or application secrets. Runtime requests may not
install dependencies. Only dependency-free Node projects and Python projects
whose dependencies already exist in this image can execute successfully.

Build and write the ignored local environment file from the repository root:

```bash
./scripts/build_stage2c_runtime.sh
source ./scripts/load_stage2c_runtime.sh
```

The build script prints and records the exact resulting image ID. Rebuilding
after any Dockerfile or base-digest change requires repinning Astra to the new
observed image ID and rerunning the real Docker integration suite.
