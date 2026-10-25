"""Pruebas del adaptador Ollama: limite de texto segun num_ctx y contadores de rendimiento."""
from types import SimpleNamespace

from domain.entities.documento import TipoDocumento
from infrastructure.llm.ollama_qwen_adapter import OllamaQwenAdapter


def _adaptador(num_ctx=8192, num_predict=1024):
    a = OllamaQwenAdapter("http://localhost:1", "m", num_ctx=num_ctx, num_predict=num_predict)
    return a


def test_limite_texto_por_defecto_sigue_siendo_8000():
    assert _adaptador()._limite_texto() == 8000


def test_limite_texto_se_reduce_con_num_ctx_pequeno():
    assert _adaptador(num_ctx=4096)._limite_texto() == (4096 - 1024 - 1000) * 3
    assert _adaptador(num_ctx=1024)._limite_texto() == 2000  # piso


def test_prompt_respeta_limite_y_registra_metricas():
    a = _adaptador(num_ctx=4096)
    visto = {}

    def chat(**kw):
        visto["prompt"] = kw["messages"][1]["content"]
        visto["options"] = kw["options"]
        return SimpleNamespace(
            message=SimpleNamespace(content='{"curp": "COCD870304MDGRRL02"}'),
            prompt_eval_count=100, prompt_eval_duration=1, eval_count=20,
            eval_duration=2, load_duration=3, total_duration=6,
        )

    a._client = SimpleNamespace(chat=chat)
    campos = a.extraer_campos("§" * 20000, TipoDocumento.INE)
    assert campos["curp"] == "COCD870304MDGRRL02"
    assert visto["options"]["num_ctx"] == 4096
    # 20000 caracteres de entrada se recortan al limite calculado
    assert visto["prompt"].count("§") == a._limite_texto()
    assert a.ultimas_metricas == {
        "prompt_eval_count": 100, "prompt_eval_duration": 1, "eval_count": 20,
        "eval_duration": 2, "load_duration": 3, "total_duration": 6,
    }
