"""Los precios del pase viven en configuración, no en el código.

Cero apaga la vía de pago entera, igual que `gacha_base_url` vacío apaga el gacha. Es lo que
permite desplegar esto sin haber decidido el precio: la compra simplemente no se ofrece.
"""
from app.config import Settings, avisar_precios_raros


def test_por_defecto_la_via_de_pago_esta_APAGADA():
    s = Settings()
    assert s.tracker_pass_7d_usdc == 0.0
    assert s.tracker_pass_30d_usdc == 0.0


def test_el_aviso_salta_cuando_el_pase_LARGO_sale_peor_por_dia():
    # 7 días a 10 son 1.43/día; 30 días a 50 son 1.67/día. Comprar el largo sería tirar dinero,
    # y eso solo lo descubre el cliente que eche la cuenta.
    assert avisar_precios_raros(10.0, 50.0) is not None


def test_sin_aviso_cuando_el_largo_sale_mejor():
    assert avisar_precios_raros(10.0, 30.0) is None


def test_sin_aviso_cuando_alguno_esta_apagado():
    # Con la vía apagada no hay nada que comparar, y avisar sería ruido en cada arranque.
    assert avisar_precios_raros(0.0, 0.0) is None
    assert avisar_precios_raros(10.0, 0.0) is None
    assert avisar_precios_raros(0.0, 30.0) is None


def test_el_mismo_precio_por_dia_no_avisa():
    # 7 a 7 y 30 a 30 son 1.0/día los dos. No es un chollo, pero no es un error.
    assert avisar_precios_raros(7.0, 30.0) is None
