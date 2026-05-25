from __future__ import annotations

import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.llm import local_provider, onnx_provider
from app.llm.local_provider import LocalBackend
from app.llm.onnx_provider import OnnxBackend, OnnxProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.registry import get_provider_for_mode


def _write_genai_bundle(path: Path, *, model_file: str = "model.int4.onnx") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "genai_config.json").write_text("{}", encoding="utf-8")
    (path / model_file).write_bytes(b"placeholder")
    return path


def _mock_onnx_modules(monkeypatch, *, providers: list[str] | None = None):
    fake_genai = types.SimpleNamespace()
    fake_onnxruntime = types.SimpleNamespace(get_available_providers=lambda: providers or ["DmlExecutionProvider"])
    monkeypatch.setitem(sys.modules, "onnxruntime_genai", fake_genai)
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_onnxruntime)
    return fake_genai, fake_onnxruntime


def _clear_onnx_env(monkeypatch):
    for key in (
        "MARVIS_ONNX_MODEL_PATH",
        "MAVRIS_ONNX_MODEL_PATH",
        "MARVIS_ONNX_MODELS_DIR",
        "MAVRIS_ONNX_MODELS_DIR",
        "MARVIS_ONNX_EXECUTION_PROVIDER",
        "MARVIS_ONNX_DIRECTML_DEVICE_ID",
    ):
        monkeypatch.delenv(key, raising=False)


def test_detect_onnx_backend_without_package(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "model")
    monkeypatch.setitem(sys.modules, "onnxruntime_genai", None)
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        types.SimpleNamespace(get_available_providers=lambda: ["DmlExecutionProvider"]),
    )

    backend = onnx_provider.detect_onnx_backend(model_path=str(model))

    assert backend is None


def test_detect_onnx_backend_without_model(monkeypatch, tmp_path: Path):
    _clear_onnx_env(monkeypatch)
    _mock_onnx_modules(monkeypatch)
    monkeypatch.setenv("MARVIS_ONNX_MODELS_DIR", str(tmp_path / "empty-models"))
    (tmp_path / "empty-models").mkdir()

    backend = onnx_provider.detect_onnx_backend()

    assert backend is None


def test_health_snapshot_structure(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "model")
    _mock_onnx_modules(monkeypatch, providers=["DmlExecutionProvider", "CPUExecutionProvider"])

    snapshot = onnx_provider.health_snapshot(model_path=str(model))

    assert snapshot == {
        "available": True,
        "kind": "onnx-directml",
        "model_path": str(model),
        "execution_provider": "DmlExecutionProvider",
        "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
        "generation_runtime": "onnxruntime_genai",
        "model_family": "",
        "provider_options": {},
    }


def test_onnx_provider_fallback_on_import_error(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "model")
    monkeypatch.setitem(sys.modules, "onnxruntime_genai", None)
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        types.SimpleNamespace(get_available_providers=lambda: ["DmlExecutionProvider"]),
    )

    backend = onnx_provider.detect_onnx_backend(model_path=str(model))
    snapshot = onnx_provider.health_snapshot(model_path=str(model))

    assert backend is None
    assert snapshot["available"] is False
    assert "onnxruntime-genai" in snapshot["error"].lower()


def test_detect_onnx_backend_prefers_directml_provider(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "Qwen2.5-3B-Instruct-ONNX" / "int4")

    monkeypatch.setattr(onnx_provider, "_is_genai_runtime_available", lambda: True)
    monkeypatch.setattr(onnx_provider, "_available_execution_providers", lambda: ["CPUExecutionProvider", "DmlExecutionProvider"])

    backend = onnx_provider.detect_onnx_backend(model_path=str(model))

    assert backend is not None
    assert backend.kind == "onnx-directml"
    assert backend.execution_provider == "DmlExecutionProvider"
    assert backend.model_path == str(model)
    assert backend.model_family == "qwen2.5"


def test_appsettings_reads_onnx_fields_from_sources(monkeypatch, tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
llm:
  onnx_model_path: C:/models/from-yaml
  onnx_execution_provider: OpenVINO
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MARVIS_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("MARVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("MAVRIS_ONNX_MODEL_PATH", "C:/models/from-env")
    monkeypatch.setenv("MAVRIS_ONNX_EXECUTION_PROVIDER", "CPU")

    settings = AppSettings.from_sources()

    assert settings.onnx_model_path == "C:/models/from-env"
    assert settings.onnx_execution_provider == "CPU"


def test_detect_onnx_backend_uses_appsettings_model_path_and_provider(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "model")
    settings = AppSettings(onnx_model_path=str(model), onnx_execution_provider="OpenVINO")

    monkeypatch.setattr(onnx_provider, "_is_genai_runtime_available", lambda: True)
    monkeypatch.setattr(
        onnx_provider,
        "_available_execution_providers",
        lambda: ["DmlExecutionProvider", "OpenVINOExecutionProvider", "CPUExecutionProvider"],
    )

    backend = onnx_provider.detect_onnx_backend(settings)

    assert backend is not None
    assert backend.kind == "onnx-openvino"
    assert backend.execution_provider == "OpenVINOExecutionProvider"
    assert backend.model_path == str(model)


def test_detect_onnx_backend_prefers_configured_cpu(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "model")
    settings = AppSettings(onnx_model_path=str(model), onnx_execution_provider="CPU")

    monkeypatch.setattr(onnx_provider, "_is_genai_runtime_available", lambda: True)
    monkeypatch.setattr(
        onnx_provider,
        "_available_execution_providers",
        lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )

    backend = onnx_provider.detect_onnx_backend(settings)

    assert backend is not None
    assert backend.kind == "onnx-cpu"
    assert backend.execution_provider == "CPUExecutionProvider"


def test_detect_onnx_backend_reports_unavailable_without_runtime(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "model")
    monkeypatch.setattr(onnx_provider, "_is_genai_runtime_available", lambda: False)

    backend = onnx_provider.detect_onnx_backend(model_path=str(model))
    snapshot = onnx_provider.health_snapshot(model_path=str(model))

    assert backend is None
    assert snapshot["available"] is False
    assert "onnxruntime-genai" in snapshot["error"].lower()


def test_privacy_mode_prefers_onnx_provider_when_available(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "model")
    monkeypatch.setattr(
        "app.llm.registry.detect_onnx_backend",
        lambda settings=None: OnnxBackend(
            kind="onnx-directml",
            model_path=str(model),
            execution_provider="DmlExecutionProvider",
            available_providers=["DmlExecutionProvider", "CPUExecutionProvider"],
        ),
    )
    monkeypatch.setattr(
        "app.llm.registry.detect_local_backend",
        lambda: LocalBackend("ollama", "http://127.0.0.1:11434/v1", ["qwen2"]),
    )
    settings = AppSettings(provider_name="mock", base_url="", mode="privacy", model=str(model))

    provider = get_provider_for_mode(settings, task="planner")

    assert isinstance(provider, OnnxProvider)
    assert provider.backend.kind == "onnx-directml"


def test_privacy_mode_falls_back_to_http_local_when_onnx_unavailable(monkeypatch):
    monkeypatch.setattr("app.llm.registry.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr(
        "app.llm.registry.detect_local_backend",
        lambda: LocalBackend("lmstudio", "http://127.0.0.1:1234/v1", ["local-model"]),
    )
    settings = AppSettings(provider_name="mock", base_url="", mode="privacy", model="")

    provider = get_provider_for_mode(settings, task="planner")

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.settings.provider_name == "lmstudio"


def test_onnx_provider_chat_uses_genai_runtime(monkeypatch, tmp_path: Path):
    model_dir = _write_genai_bundle(tmp_path / "genai-model")
    backend = OnnxBackend(
        kind="onnx-directml",
        model_path=str(model_dir),
        execution_provider="DmlExecutionProvider",
        available_providers=["DmlExecutionProvider"],
        provider_options={"device_id": "0"},
    )

    class _Config:
        def __init__(self, path):
            self.path = path
            self.providers = []
            self.options = {}

        def clear_providers(self):
            self.providers.clear()

        def append_provider(self, provider):
            self.providers.append(provider)

        def set_provider_option(self, key, value):
            self.options[key] = value

    class _Tokenizer:
        def __init__(self, model):
            self.model = model

        def create_stream(self):
            return _Stream()

        def encode(self, prompt):
            return [1]

    class _Stream:
        def decode(self, token):
            return {101: "ok", 102: "!"}.get(token, "")

    class _Params:
        def __init__(self, model):
            self.model = model
            self.options = {}

        def set_search_options(self, **kwargs):
            self.options = kwargs

    class _Generator:
        def __init__(self, model, params):
            self.tokens = [101, 102]
            self.index = 0
            self.params = params

        def append_tokens(self, tokens):
            self.input_tokens = tokens

        def is_done(self):
            return self.index >= len(self.tokens)

        def compute_logits(self):
            self.computed = True

        def generate_next_token(self):
            pass

        def get_next_tokens(self):
            token = self.tokens[self.index]
            self.index += 1
            return [token]

    fake_genai = types.SimpleNamespace(
        Config=_Config,
        Model=lambda config: {"config": config},
        Tokenizer=_Tokenizer,
        GeneratorParams=_Params,
        Generator=_Generator,
    )
    monkeypatch.setitem(sys.modules, "onnxruntime_genai", fake_genai)
    provider = OnnxProvider(AppSettings(mode="privacy", model=str(model_dir), max_tokens=8), backend)
    assert provider._genai_model_path() == str(model_dir)

    import asyncio

    text = asyncio.run(provider.chat([{"role": "user", "content": "say ok"}]))

    assert text == "ok!"


def test_onnx_provider_configures_provider_options(tmp_path: Path):
    model_dir = _write_genai_bundle(tmp_path / "genai-model")
    backend = OnnxBackend(
        kind="onnx-directml",
        model_path=str(model_dir),
        execution_provider="DmlExecutionProvider",
        available_providers=["DmlExecutionProvider"],
        provider_options={"device_id": "1"},
    )

    class _Config:
        def __init__(self) -> None:
            self.providers = ["CPUExecutionProvider"]
            self.options = {}

        def clear_providers(self):
            self.providers.clear()

        def append_provider(self, provider):
            self.providers.append(provider)

        def set_provider_option(self, key, value):
            self.options[key] = value

    config = _Config()
    provider = OnnxProvider(AppSettings(mode="privacy", model=str(model_dir)), backend)

    provider._configure_execution_provider(config)

    assert config.providers == ["DmlExecutionProvider"]
    assert config.options == {"device_id": "1"}


def test_detect_onnx_backend_discovers_quantized_qwen_bundle(monkeypatch, tmp_path: Path):
    generic = _write_genai_bundle(tmp_path / "models" / "generic-model", model_file="model.onnx")
    qwen = _write_genai_bundle(
        tmp_path / "models" / "Qwen2.5-3B-Instruct-ONNX" / "cpu_and_mobile" / "cpu-int4-rtn-block-32-acc-level-4",
        model_file="model.int4.onnx",
    )

    monkeypatch.setenv("MARVIS_ONNX_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(onnx_provider, "_is_genai_runtime_available", lambda: True)
    monkeypatch.setattr(onnx_provider, "_available_execution_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])

    backend = onnx_provider.detect_onnx_backend()

    assert backend is not None
    assert backend.model_path == str(qwen)
    assert backend.model_path != str(generic)
    assert backend.model_family == "qwen2.5"


def test_detect_onnx_backend_accepts_onnx_file_inside_genai_bundle(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "bundle")
    weight = model / "model.int4.onnx"

    monkeypatch.setattr(onnx_provider, "_is_genai_runtime_available", lambda: True)
    monkeypatch.setattr(onnx_provider, "_available_execution_providers", lambda: ["DmlExecutionProvider"])

    backend = onnx_provider.detect_onnx_backend(model_path=str(weight))

    assert backend is not None
    assert backend.model_path == str(model)


def test_detect_onnx_backend_rejects_bare_onnx_file(monkeypatch, tmp_path: Path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"placeholder")

    monkeypatch.setattr(onnx_provider, "_is_genai_runtime_available", lambda: True)
    monkeypatch.setattr(onnx_provider, "_available_execution_providers", lambda: ["DmlExecutionProvider"])

    backend = onnx_provider.detect_onnx_backend(model_path=str(model))
    snapshot = onnx_provider.health_snapshot(model_path=str(model))

    assert backend is None
    assert snapshot["available"] is False
    assert "genai model path" in snapshot["error"].lower() or "usable genai" in snapshot["error"].lower()


def test_detect_onnx_backend_honors_forced_directml_provider(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "model")

    monkeypatch.setenv("MARVIS_ONNX_EXECUTION_PROVIDER", "directml")
    monkeypatch.setenv("MARVIS_ONNX_DIRECTML_DEVICE_ID", "2")
    monkeypatch.setattr(onnx_provider, "_is_genai_runtime_available", lambda: True)
    monkeypatch.setattr(onnx_provider, "_available_execution_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(onnx_provider, "_genai_reports_provider_available", lambda kind: kind == "onnx-directml")

    backend = onnx_provider.detect_onnx_backend(model_path=str(model))

    assert backend is not None
    assert backend.kind == "onnx-directml"
    assert backend.execution_provider == "DmlExecutionProvider"
    assert "DmlExecutionProvider" in backend.available_providers
    assert backend.provider_options == {"device_id": "2"}


def test_openvino_backend_does_not_use_directml_device_option(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "model")

    monkeypatch.setenv("MARVIS_ONNX_DIRECTML_DEVICE_ID", "2")
    monkeypatch.setattr(onnx_provider, "_is_genai_runtime_available", lambda: True)
    monkeypatch.setattr(onnx_provider, "_available_execution_providers", lambda: ["OpenVINOExecutionProvider", "CPUExecutionProvider"])

    backend = onnx_provider.detect_onnx_backend(model_path=str(model))

    assert backend is not None
    assert backend.kind == "onnx-openvino"
    assert backend.provider_options == {}


def test_local_health_snapshot_includes_onnx_probe(monkeypatch):
    seen = {}

    def fake_detect(settings=None):
        seen["settings"] = settings
        return OnnxBackend(
            kind="onnx-openvino",
            model_path="C:/models/qwen.onnx",
            execution_provider="OpenVINOExecutionProvider",
            available_providers=["OpenVINOExecutionProvider", "CPUExecutionProvider"],
        )

    monkeypatch.setattr(
        "app.llm.local_provider.detect_onnx_backend",
        fake_detect,
    )
    monkeypatch.setattr("app.llm.local_provider.detect_local_backend", lambda **kwargs: None)

    settings = AppSettings(onnx_execution_provider="OpenVINO")
    snapshot = local_provider.health_snapshot(settings)

    assert snapshot["available"] is True
    assert snapshot["selected_backend"]["kind"] == "onnx-openvino"
    assert snapshot["selected_backend"]["execution_provider"] == "OpenVINOExecutionProvider"
    assert snapshot["probe_order"][0] == "onnx"
    assert seen["settings"] is settings


def test_settings_onnx_status_route_reports_snapshot(monkeypatch, tmp_path: Path):
    model = _write_genai_bundle(tmp_path / "model")

    monkeypatch.setenv("MARVIS_ONNX_MODEL_PATH", str(model))
    monkeypatch.setattr(onnx_provider, "_is_genai_runtime_available", lambda: True)
    monkeypatch.setattr(onnx_provider, "_available_execution_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])

    from app.main import create_app

    response = TestClient(create_app()).get("/api/settings/onnx/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["execution_provider"] == "DmlExecutionProvider"
    assert payload["available_providers"] == ["DmlExecutionProvider", "CPUExecutionProvider"]
    assert payload["model_path"] == str(model)
