import os
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///battlearena.db"
    chain_source: str = "mock"
    solana_rpc_url: str = "https://api.devnet.solana.com"
    program_id: str = ""
    elo_start: int = 1200
    elo_k: int = 32
    session_ttl: int = 3600
    cors_origins: List[str] = []
    gacha_base_url: str = "https://dev-gacha.collectorcrypt.com"  # vacío => gacha deshabilitado (kill-switch)
    gacha_api_key: str = ""  # opcional; devnet es keyless, solo necesario si el entorno lo exige (p.ej. mainnet)
    # Interruptor del ingestor del EV tracker. Encendido por defecto, pero como en este proyecto
    # subir a master ES desplegar, tiene que poder apagarse desde el .env del mini PC sin tocar
    # código: es un proceso permanente contra un servicio de terceros.
    ev_tracker_enabled: bool = True
    # Host público de metadata/imágenes de Collector Crypt por mint (keyless). Devnet por defecto;
    # en mainnet: https://nft.collectorcrypt.com. env: CC_NFT_BASE_URL
    cc_nft_base_url: str = "https://nft-dev.collectorcrypt.com"
    privy_app_id: str = ""
    privy_jwks_url: str = "https://auth.privy.io/api/v1/apps/{app_id}/jwks.json"
    privy_app_secret: str = ""
    privy_auth_key: str = ""
    privy_solana_caip2: str = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"  # devnet default
    privy_quorum_id: str = ""
    cc_usdc_mint: str = "Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr"
    gimmighoul_per_usdc: float = 0.5  # battles/royale loyalty rate; env: GIMMIGHOUL_PER_USDC
    # El gacha renta menos que una batalla a propósito: se premia jugar contra alguien, no abrir
    # sobres en solitario. Si se cambia cualquiera de los dos, hay que tocar también lo que se le
    # promete al jugador en src/ui/screens/Help/helpContent.ts y OnboardingTutorial.tsx, que
    # llevan las cifras escritas a mano.
    gimmighoul_per_usdc_gacha: float = 0.01  # env: GIMMIGHOUL_PER_USDC_GACHA
    # Platform fee on battles: pct per player over the buyback value of the winner's loot,
    # capped at battle_fee_pct_cap total. Collected in USDC from the winner's wallet after
    # settle. fee_wallet_address empty → falls back to privy_operator_address; both empty →
    # collection is skipped (kill-switch). env: BATTLE_FEE_PCT_PER_PLAYER / BATTLE_FEE_PCT_CAP
    # / FEE_WALLET_ADDRESS
    battle_fee_pct_per_player: float = 0.005
    battle_fee_pct_cap: float = 0.03
    # Mínimo para que un referidor pueda reclamar su rev-share. Agrega el polvo de muchas
    # batallas en un solo pago: sin mínimo, cada claim costaría más en fees de red que el importe.
    referral_claim_min_base_units: int = 5_000_000  # $5; env: REFERRAL_CLAIM_MIN_BASE_UNITS
    # Wallet desde la que se pagan los claims de rev-share. Es la MISMA a la que aterriza el rake,
    # así que tiene que ser una wallet de Privy firmable (no basta una dirección suelta como
    # fee_wallet_address). Está sin decidir: mientras esté vacía, el claim responde 503 en vez de
    # tirar del operador — el operador ya paga el rent de las cartas y la siembra de escrows, y
    # convertirlo además en la caja de los referidos lo haría punto único de fallo de tres cosas.
    referral_payout_wallet_id: str = ""    # env: REFERRAL_PAYOUT_WALLET_ID
    referral_payout_address: str = ""      # env: REFERRAL_PAYOUT_ADDRESS
    # Chat announcements: a gacha hit >= this multiple of the pull cost, and a battle winner whose
    # haul >= this multiple of the entry, get a highlight in the lobby chat. env: HIT/WINNER_ANNOUNCE_MULT
    hit_announce_mult: float = 3.0
    winner_announce_mult: float = 4.0
    fee_wallet_address: str = "5DfUc9vcvLBNCTrzWXsXrEdD8x8DoPuYxLYoAytXuub9"
    privy_operator_wallet_id: str = ""
    privy_operator_address: str = ""
    escrow_seed_lamports: int = 10_000_000
    # Inventario COMPARTIDO de wallets de escrow (ver services/escrow_inventory.py). Vacío = no se
    # usa y todo funciona como antes. La ruta debe ser ABSOLUTA: una relativa se resuelve contra el
    # directorio de trabajo, y dos inventarios divergentes repartirían la misma wallet dos veces.
    escrow_inventory_url: str = ""    # env: ESCROW_INVENTORY_URL
    # Endpoints SOLO de desarrollo/test (p.ej. /pack-battles/{id}/join-bot, que mete un bot
    # financiado en un lobby SIN autenticación y mueve USDC on-chain). Deshabilitados por
    # defecto: en producción NUNCA deben estar activos. env: DEV_ENDPOINTS_ENABLED=true
    dev_endpoints_enabled: bool = False
    # Anti-abuso de retiros: el operador paga el gas y la renta de la ATA destino de cada
    # withdraw. Sin un mínimo + rate-limit, un atacante haría miles de retiros de 1 unidad a
    # direcciones nuevas para drenar el SOL del operador (renta de ATA ~0.002 SOL c/u).
    min_withdraw_usdc: float = 1.0        # retiro mínimo (USDC); env: MIN_WITHDRAW_USDC
    withdraw_rate_limit: int = 5          # nº máx. de retiros por wallet y ventana
    withdraw_rate_window_s: float = 60.0  # ventana del rate-limit de retiros (segundos)
    # Tips entre jugadores. El mínimo existe por lo mismo que el del withdraw: si el destinatario
    # todavía no tiene cuenta de USDC, el operador paga su renta (~0.002 SOL), así que sin mínimo
    # se le drena a base de propinas minúsculas a jugadores nuevos. Mismo valor que
    # min_withdraw_usdc, y por el mismo motivo: por debajo de 1 USDC sale barato hacer gastar SOL
    # al operador a costa de destinatarios nuevos. El rate-limit es contra el spam social, sobre
    # todo desde el chat.
    # Interruptor de las propinas. APAGADO por defecto: la funcionalidad está entera y probada,
    # pero mueve dinero de verdad entre jugadores y todavía no la ha usado ninguno. Se enciende a
    # propósito con TIPS_ENABLED=true, no por descuido al desplegar. Apagarlo NO borra nada: el
    # endpoint responde 503 y el frontend esconde los accesos, y el historial de `tips` sigue ahí.
    tips_enabled: bool = False            # env: TIPS_ENABLED
    min_tip_usdc: float = 1.0             # propina mínima (USDC); env: MIN_TIP_USDC
    tip_rate_limit: int = 10              # nº máx. de tips por wallet y ventana
    tip_rate_window_s: float = 60.0       # ventana del rate-limit de tips (segundos)
    # Rate-limit del gacha por wallet (ventana fija de 60s). Solo cuenta INICIAR una tirada
    # (generate-pack / yolo) y buyback; submit-tx y open-pack (polleada) NO cuentan → una
    # tirada normal = 1 hit. Subir para pruebas o picos de tráfico. env: GACHA_RATE_LIMIT
    gacha_rate_limit: int = 60
    # Fee de plataforma sobre cada withdraw de USDC: pct del importe retirado, descontado (el
    # destino recibe el resto) y enviado al fee_wallet_address (fallback al operador). 0 → sin fee.
    # env: WITHDRAW_FEE_PCT
    withdraw_fee_pct: float = 0.01
    # Launch week: restringe la CREACIÓN de Battle Royale a estas wallets (Privy embedded
    # Solana, base58, coma-separadas). Vacío = abierto a todos (comportamiento por defecto).
    # env: ROYALE_CREATOR_ALLOWLIST
    royale_creator_allowlist: str = ""

    # Wallets con acceso PERMANENTE al Machine Tracker, sin tener que apostar los 100 USDC de la
    # ventana rodante (Privy embedded Solana, base58, coma-separadas). Para las cuentas de la casa:
    # probar el tracker en producción no puede exigir apostar de verdad cada semana. Vacío = la
    # puerta normal para todos. Vive AQUÍ y no en la base a propósito: así solo se toca desplegando,
    # y no hay ninguna vía por la que un jugador se añada solo. env: TRACKER_ACCESS_ALLOWLIST
    tracker_access_allowlist: str = ""

    # Pase de pago del Machine Tracker, la vía alternativa al wager de 100 USDC.
    # CERO APAGA LA COMPRA, igual que `gacha_base_url` vacío apaga el gacha: así esto se puede
    # desplegar antes de haber decidido el precio, sin ofrecer nada a medias.
    tracker_pass_7d_usdc: float = 0.0      # env: TRACKER_PASS_7D_USDC
    tracker_pass_30d_usdc: float = 0.0     # env: TRACKER_PASS_30D_USDC

    @property
    def royale_creator_allowlist_set(self) -> set[str]:
        return {w.strip() for w in self.royale_creator_allowlist.split(",") if w.strip()}

    @property
    def tracker_access_allowlist_set(self) -> set[str]:
        return {w.strip() for w in self.tracker_access_allowlist.split(",") if w.strip()}


def avisar_precios_raros(s7: float, s30: float) -> Optional[str]:
    """Si el pase de 30 días sale MÁS CARO por día que el de 7, devuelve el aviso.

    No es un error que deba impedir arrancar: el precio es una decisión de negocio y quizá alguien
    lo quiere así por un tiempo. Pero es un fallo de configuración que, sin este aviso, solo
    descubre el cliente que eche la cuenta, y para entonces ya ha comprado el caro.
    """
    if s7 <= 0 or s30 <= 0:
        return None                       # con la vía apagada no hay nada que comparar
    if s30 / 30.0 > s7 / 7.0:
        return (f"TRACKER_PASS_30D_USDC ({s30}) sale a {s30 / 30:.3f}/día, más caro que "
                f"TRACKER_PASS_7D_USDC ({s7}) a {s7 / 7:.3f}/día")
    return None


def get_settings() -> Settings:
    # Devnet and mainnet run as separate stacks (different ports + DB). The mainnet stack sets
    # APP_NETWORK=mainnet, which layers backend/.env.mainnet (network-only overrides: RPC, CC gacha
    # host, USDC mint, CAIP2, DATABASE_URL) ON TOP of the shared backend/.env (secrets + defaults).
    # Devnet uses .env alone. Real env vars still win over both.
    if os.environ.get("APP_NETWORK", "").lower() == "mainnet":
        return Settings(_env_file=(".env", ".env.mainnet"))
    return Settings()
