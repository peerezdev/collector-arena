"""Pin la lectura del bundle de `scripts/fetch_cards_airdrop.py`, que corre sin red mockeando
`_get` (y en el primer test también `json.loads`, ver más abajo).

El escaneo de la comilla de cierre depende de la PARIDAD de la racha de backslashes que la
preceden, no de si el carácter inmediatamente anterior es un backslash: una racha par es un
backslash escapado seguido de un cierre real; una impar es un apóstrofo escapado dentro del
propio contenido. Antes del fix, el código solo miraba un carácter, así que una racha par se
confundía con un apóstrofo escapado y el escaneo se comía de más de lo debido.

El primer test no puede validar contra `json.loads` real porque, por construcción, cualquier
entrada en la que el cierre verdadero tenga una racha par y positiva deja el `crudo` terminado
en un backslash suelto tras el unescape (siempre inválido como JSON, tenga el fix o no) — así
que se mockea `json.loads` para capturar el `crudo` exacto que produjo el escaneo, sin depender
de que sea JSON válido. Si el escaneo volviera a mirar solo un carácter, este test fallaría
porque el crudo capturado incluiría la basura posterior a la comilla real. El segundo test sí
es un round-trip real: un apóstrofo escapado (racha impar) dentro de un valor, con el cierre
real más adelante.
"""
import importlib.util
import json as json_module
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "fetch_cards_airdrop.py"
BS = chr(92)  # backslash crudo, para no perderse contando barras en el propio código del test
INDEX_HTML = '<script src="/js/bundle.deadbeef123.min.js"></script>'


def _cargar_script():
    """Carga el script como módulo sin ejecutar `main()` (va detrás de `if __name__ ==
    "__main__"`), así que importarlo no abre ningún socket."""
    spec = importlib.util.spec_from_file_location("fetch_cards_airdrop", SCRIPT_PATH)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def script(monkeypatch):
    modulo = _cargar_script()
    monkeypatch.setattr(modulo, "_get", lambda url: INDEX_HTML)  # el bundle lo pisa cada test
    return modulo


def test_el_cierre_real_con_racha_par_de_backslashes_para_ahi_y_no_sigue_de_largo(script, monkeypatch):
    # Tras el contenido "real", dos backslashes crudos (racha PAR) y la comilla de cierre
    # verdadera. Después de esa comilla hay basura con OTRA comilla más adelante: si el
    # escaneo (como el código de antes del fix) se dejara engañar por el backslash inmediato
    # y siguiera de largo, se comería esa basura y el crudo capturado sería mucho más largo.
    bundle = (
        "var a=1;\n"
        'JSON.parse(\'[{"handle":"W1","amount":"1"}]'
        + BS + BS
        + "'JUNK_QUE_NO_DEBERIA_ENTRAR');\n"
    )
    monkeypatch.setattr(script, "_get", lambda url: INDEX_HTML if url.endswith("/") else bundle)

    capturado = {}

    def _loads_falso(s):
        capturado["crudo"] = s
        return ["sentinela"]

    monkeypatch.setattr(script.json, "loads", _loads_falso)

    resultado = script.lista_del_bundle()

    # Tras el unescape, las dos rachas de backslash crudas colapsan en una sola: el crudo
    # correcto termina justo ahí, SIN la basura posterior.
    assert capturado["crudo"] == '[{"handle":"W1","amount":"1"}]' + BS
    assert resultado == ["sentinela"]


def test_un_apostrofo_escapado_dentro_de_un_valor_no_corta_la_lectura(script, monkeypatch):
    # Racha IMPAR (un solo backslash) delante del apóstrofo: es un apóstrofo literal dentro
    # del handle, no el cierre. El escaneo debe seguir de largo y encontrar el cierre real
    # más adelante, y el resultado final debe parsear tal cual.
    entradas = [{"handle": "O'Brien", "amount": "1", "url": "https://x/?index=0&proof=ABC"}]
    crudo_json = json_module.dumps(entradas)
    escapado = crudo_json.replace("'", BS + "'")  # así escapa CC los apóstrofos en su bundle
    bundle = f"var a=1;\nJSON.parse('{escapado}');\n"
    monkeypatch.setattr(script, "_get", lambda url: INDEX_HTML if url.endswith("/") else bundle)

    resultado = script.lista_del_bundle()
    assert resultado == entradas


def test_dos_asignaciones_para_la_misma_wallet_abortan_sin_escribir(tmp_path, monkeypatch):
    """Finding 2 (revisión): el árbol de Gumdrop está indexado por index, no por wallet. Sin
    esta guarda, la segunda hoja pisaría a la primera en silencio."""
    modulo = _cargar_script()
    monkeypatch.setattr(modulo, "SALIDA", tmp_path / "no_deberia_escribirse.json")

    # "1"*32 es la wallet del System Program: base58 válida, así hoja() no revienta al construir
    # la hoja aunque verificar_proof esté mockeada más abajo y no importe si "prueba" nada real.
    WALLET = "1" * 32
    entradas = [
        {"handle": WALLET, "amount": "1", "url": f"https://x/?index=0&proof={WALLET}"},
        {"handle": WALLET, "amount": "2", "url": f"https://x/?index=1&proof={WALLET}"},
    ]

    monkeypatch.setattr(modulo, "root_on_chain", lambda: bytes(32))
    monkeypatch.setattr(modulo, "lista_del_bundle", lambda: entradas)
    monkeypatch.setattr(modulo, "verificar_proof", lambda hoja_bytes, proof, root: True)

    with pytest.raises(SystemExit, match="aparece dos veces"):
        modulo.main()

    assert not modulo.SALIDA.exists()
