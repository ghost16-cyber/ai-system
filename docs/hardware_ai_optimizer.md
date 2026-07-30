# Hardware-Aware AI Optimizer

The hardware-aware AI optimizer is Astra's first low-VRAM training subsystem.
It is intentionally small in this phase: it detects local hardware and turns the
result into conservative training recommendations.

## Current Scope

- Detect CPU name and core count.
- Detect system RAM where the platform exposes it.
- Detect storage capacity for the configured workspace root.
- Detect PyTorch installation and CUDA build metadata when PyTorch is installed.
- Detect GPU and VRAM through PyTorch CUDA first, then `nvidia-smi` when
  available.
- Recommend a low-VRAM training profile for small laptop GPUs.

## API

```http
GET /hardware-ai/report
```

The response contains two sections:

- `report`: observed hardware facts.
- `recommendations`: safe defaults and warnings for training AI models on the
  detected machine.

## Python API

```python
from backend.app.hardware_ai_optimizer import (
    probe_hardware,
    recommend_training_settings,
)

report = probe_hardware(".")
recommendations = recommend_training_settings(report)
```

## Next Steps

- Add a PyTorch script analyzer for batch size, image size, AMP, and DataLoader
  settings.
- Add an automatic batch-size finder that performs a safe dry run.
- Add patch proposals for common low-VRAM fixes.
- Add training monitor output for CUDA out-of-memory recovery and checkpointing.
